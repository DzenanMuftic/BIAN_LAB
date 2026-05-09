package com.example.bian;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;

@Component
public class KafkaConsumer {

    private static final Logger log = LoggerFactory.getLogger(KafkaConsumer.class);

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;

    public KafkaConsumer(StringRedisTemplate redisTemplate, ObjectMapper objectMapper) {
        this.redisTemplate = redisTemplate;
        this.objectMapper = objectMapper;
    }

    @KafkaListener(topics = "${app.kafka.bookings-topic}", groupId = "bian-balance-calculator")
    public void onBookingEvent(String message) {
        try {
            JsonNode root = objectMapper.readTree(message);
            JsonNode payload = root.path("payload");
            JsonNode after = payload.path("after");

            if (after.isMissingNode() || after.isNull()) {
                return;
            }

            String accountId = text(after, "ACCOUNT_ID");
            String operator = text(after, "OPERATOR");
            BigDecimal amount = decimal(after, "AMOUNT");

            if (accountId == null || amount == null) {
                return;
            }

            if ("BIAN_API".equalsIgnoreCase(operator)) {
                return;
            }

            BigDecimal eod = readAmountOrZero(Application.CACHE_EOD_PREFIX + accountId);
            BigDecimal bookings = readAmountOrZero(Application.CACHE_BOOKINGS_PREFIX + accountId).add(amount);
            BigDecimal current = eod.add(bookings).setScale(2, RoundingMode.HALF_UP);

            redisTemplate.opsForValue().set(Application.CACHE_BOOKINGS_PREFIX + accountId, bookings.toPlainString());
            redisTemplate.opsForValue().set(Application.CACHE_CURRENT_PREFIX + accountId, current.toPlainString());

            log.info("Updated instant balance for account {} to {}", accountId, current.toPlainString());
        } catch (Exception ex) {
            log.error("Failed to process BOOKINGS event", ex);
        }
    }

    private String text(JsonNode node, String field) {
        JsonNode valueNode = node.path(field);
        if (valueNode.isMissingNode() || valueNode.isNull()) {
            return null;
        }
        String value = valueNode.asText();
        return value == null || value.isBlank() ? null : value;
    }

    private BigDecimal decimal(JsonNode node, String field) {
        String value = text(node, field);
        if (value == null) {
            return null;
        }
        try {
            return new BigDecimal(value).setScale(2, RoundingMode.HALF_UP);
        } catch (NumberFormatException ex) {
            return null;
        }
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
