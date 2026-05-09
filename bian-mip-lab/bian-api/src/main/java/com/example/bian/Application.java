package com.example.bian;

import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@SpringBootApplication
@Validated
public class Application {

    public static final String CACHE_EOD_PREFIX = "balance:eod:";
    public static final String CACHE_BOOKINGS_PREFIX = "balance:bookings:";
    public static final String CACHE_CURRENT_PREFIX = "balance:current:";

    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }

    @Bean
    CommandLineRunner primeCache(JdbcTemplate jdbcTemplate, StringRedisTemplate redisTemplate) {
        return args -> jdbcTemplate.query(
            """
            SELECT b.ACCOUNT_ID,
                   b.EOD_AMOUNT,
                   NVL((SELECT SUM(k.AMOUNT)
                          FROM BOOKINGS k
                         WHERE k.ACCOUNT_ID = b.ACCOUNT_ID
                           AND TRUNC(k.BOOKING_DATE) = TRUNC(SYSDATE)
                       ), 0) AS TODAY_BOOKINGS_SUM
              FROM EOD_BALANCE b
            """,
            rs -> {
                String accountId = rs.getString("ACCOUNT_ID");
                BigDecimal eod = rs.getBigDecimal("EOD_AMOUNT").setScale(2, RoundingMode.HALF_UP);
                BigDecimal todayBookings = rs.getBigDecimal("TODAY_BOOKINGS_SUM").setScale(2, RoundingMode.HALF_UP);
                BigDecimal current = eod.add(todayBookings).setScale(2, RoundingMode.HALF_UP);

                redisTemplate.opsForValue().set(CACHE_EOD_PREFIX + accountId, eod.toPlainString());
                redisTemplate.opsForValue().set(CACHE_BOOKINGS_PREFIX + accountId, todayBookings.toPlainString());
                redisTemplate.opsForValue().set(CACHE_CURRENT_PREFIX + accountId, current.toPlainString());
            }
        );
    }
}

@RestController
class BianController {

    private final JdbcTemplate jdbcTemplate;
    private final StringRedisTemplate redisTemplate;

    BianController(JdbcTemplate jdbcTemplate, StringRedisTemplate redisTemplate) {
        this.jdbcTemplate = jdbcTemplate;
        this.redisTemplate = redisTemplate;
    }

        @GetMapping(value = "/", produces = MediaType.TEXT_HTML_VALUE)
        public ResponseEntity<String> home() {
                String html = """
                        <!doctype html>
                        <html lang=\"en\">
                        <head>
                            <meta charset=\"UTF-8\" />
                            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
                            <title>BIAN 14.0 MIP Lab</title>
                            <style>
                                body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 2rem; }
                                code { background: #f2f4f7; padding: 0.1rem 0.35rem; border-radius: 0.25rem; }
                            </style>
                        </head>
                        <body>
                            <h1>BIAN 14.0 Mobile Instant Payment Lab</h1>
                            <p>Service is running. Use these endpoints:</p>
                            <ul>
                                <li><code>GET /bian/v14/current-account/{accountId}/get-balance</code></li>
                                <li><code>POST /bian/v14/payment-order/{accountId}/initiate</code></li>
                            </ul>
                            <p>Try account: <code>BA391860123456789012</code></p>
                        </body>
                        </html>
                        """;
                return ResponseEntity.ok(html);
        }

    @GetMapping("/bian/v14/current-account/{accountId}/get-balance")
    public ResponseEntity<Map<String, Object>> getBalance(@PathVariable String accountId) {
        String current = redisTemplate.opsForValue().get(Application.CACHE_CURRENT_PREFIX + accountId);
        if (current == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of(
                "status", "NotFound",
                "message", "No cached balance available for account"
            ));
        }

        Map<String, Object> response = new HashMap<>();
        response.put("serviceDomain", "CurrentAccount");
        response.put("actionTerm", "GetBalance");
        response.put("accountId", accountId);
        response.put("asOf", LocalDateTime.now().format(DateTimeFormatter.ISO_LOCAL_DATE_TIME));
        response.put("availableBalance", current);
        response.put("currency", "EUR");
        response.put("semanticVersion", "BIAN-14.0");
        return ResponseEntity.ok(response);
    }

    @PostMapping("/bian/v14/payment-order/{accountId}/initiate")
    public ResponseEntity<Map<String, Object>> initiatePayment(
        @PathVariable String accountId,
        @Valid @RequestBody PaymentInitiateRequest request
    ) {
        BigDecimal current = readAmountOrZero(Application.CACHE_CURRENT_PREFIX + accountId);
        if (current.compareTo(request.amount()) < 0) {
            return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(Map.of(
                "serviceDomain", "PaymentOrder",
                "actionTerm", "Initiate",
                "status", "Rejected",
                "reason", "Insufficient funds",
                "accountId", accountId,
                "requestedAmount", request.amount().toPlainString(),
                "currency", request.currency(),
                "semanticVersion", "BIAN-14.0"
            ));
        }

        String paymentReference = UUID.randomUUID().toString();

        jdbcTemplate.update(
            """
            INSERT INTO BOOKINGS (ACCOUNT_ID, BOOKING_DATE, AMOUNT, CURRENCY, OPERATOR, DESCRIPTION)
            VALUES (?, SYSDATE, ?, ?, ?, ?)
            """,
            accountId,
            request.amount().negate(),
            request.currency(),
            "BIAN_API",
            "PAYMENT_ORDER:" + paymentReference
        );

        jdbcTemplate.update(
            """
            INSERT INTO OPERATOR_LOG (OPERATOR, ACTION, DETAILS)
            VALUES (?, ?, ?)
            """,
            "BIAN_API",
            "PaymentOrder.Initiate",
            "accountId=" + accountId + ";paymentReference=" + paymentReference
        );

        BigDecimal bookingsSum = readAmountOrZero(Application.CACHE_BOOKINGS_PREFIX + accountId)
            .subtract(request.amount())
            .setScale(2, RoundingMode.HALF_UP);
        BigDecimal newCurrent = current.subtract(request.amount()).setScale(2, RoundingMode.HALF_UP);

        redisTemplate.opsForValue().set(Application.CACHE_BOOKINGS_PREFIX + accountId, bookingsSum.toPlainString());
        redisTemplate.opsForValue().set(Application.CACHE_CURRENT_PREFIX + accountId, newCurrent.toPlainString());

        return ResponseEntity.status(HttpStatus.ACCEPTED).body(Map.ofEntries(
            Map.entry("serviceDomain", "PaymentOrder"),
            Map.entry("actionTerm", "Initiate"),
            Map.entry("status", "Accepted"),
            Map.entry("paymentOrderReference", paymentReference),
            Map.entry("accountId", accountId),
            Map.entry("amount", request.amount().toPlainString()),
            Map.entry("currency", request.currency()),
            Map.entry("counterpartyIban", request.counterpartyIban()),
            Map.entry("description", request.description() != null ? request.description() : ""),
            Map.entry("balanceAfterInitiation", newCurrent.toPlainString()),
            Map.entry("semanticVersion", "BIAN-14.0")
        ));
    }

    private BigDecimal readAmountOrZero(String key) {
        String value = redisTemplate.opsForValue().get(key);
        if (value == null || value.isBlank()) {
            return BigDecimal.ZERO.setScale(2, RoundingMode.HALF_UP);
        }
        try {
            return new BigDecimal(value).setScale(2, RoundingMode.HALF_UP);
        } catch (NumberFormatException ex) {
            return BigDecimal.ZERO.setScale(2, RoundingMode.HALF_UP);
        }
    }
}

record PaymentInitiateRequest(
    @NotNull @DecimalMin("0.01") BigDecimal amount,
    @NotBlank String currency,
    @NotBlank String counterpartyIban,
    String description
) {
}
