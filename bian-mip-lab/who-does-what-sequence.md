# Who Is Doing What - Sequence

This file describes the runtime responsibilities and message flow in the BIAN MIP lab.

## Actors

- User: triggers actions in the web UI
- Flask Web App: UI server, calls BIAN API and reads Oracle for history/flow dashboard
- BIAN API (Spring Boot): business endpoints and instant balance cache updates
- Oracle DB: source of truth for EOD balances, bookings, and operator log
- Debezium Connector: captures Oracle table changes
- Kafka: transports CDC events
- BIAN KafkaConsumer: consumes `BOOKINGS` CDC events
- Valkey: instant balance cache (`balance:eod:*`, `balance:bookings:*`, `balance:current:*`)

## End-to-End Sequence (Who Does What)

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant F as Flask Web App
    participant B as BIAN API
    participant V as Valkey
    participant O as Oracle DB
    participant D as Debezium
    participant K as Kafka
    participant C as BIAN KafkaConsumer

    Note over B,O,V: Startup: BIAN API primes Valkey from Oracle (EOD + same-day BOOKINGS)
    B->>O: SELECT EOD_BALANCE + SUM(today BOOKINGS)
    O-->>B: Per-account amounts
    B->>V: SET balance:eod, balance:bookings, balance:current

    rect rgb(239, 247, 255)
        Note over U,V: A) Get current balance
        U->>F: Open account dashboard
        F->>B: GET /current-account/{accountId}/get-balance
        B->>V: GET balance:current:{accountId}
        V-->>B: Current amount
        B-->>F: Balance JSON
        F-->>U: Render balance in UI
    end

    rect rgb(243, 255, 242)
        Note over U,V: B) Initiate payment order (source -> recipient)
        U->>F: Submit transfer form
        F->>B: POST /payment-order/{source}/initiate
        B->>V: Validate source and recipient in cache
        V-->>B: Current balances
        B->>O: INSERT BOOKINGS (source debit, OPERATOR=BIAN_API)
        B->>O: INSERT BOOKINGS (recipient credit, OPERATOR=BIAN_API)
        B->>O: INSERT OPERATOR_LOG (PaymentOrder.Initiate)
        B->>V: Update source/recipient bookings + current balances
        B-->>F: 202 Accepted + paymentOrderReference
        F-->>U: Show success message
    end

    rect rgb(255, 246, 238)
        Note over O,V: C) CDC refresh path for non-BIAN_API bookings
        O-->>D: BOOKINGS table change committed
        D->>K: Publish CDC event to banking.XEPDB1.BANKING.BOOKINGS
        K-->>C: Deliver booking event
        C->>C: Ignore event if OPERATOR == BIAN_API
        alt Operator is not BIAN_API
            C->>V: Read EOD + current bookings sum
            C->>V: Write updated bookings and current balance
        else Operator is BIAN_API
            C-->>C: Skip to prevent double-apply
        end
    end
```

## Responsibility Summary

- Flask Web App: user interaction, transfer request forwarding, and dashboard composition.
- BIAN API: validation, Oracle writes for payment lifecycle, and immediate Valkey cache updates.
- Oracle DB: authoritative ledger (`BOOKINGS`) and audit (`OPERATOR_LOG`).
- Debezium + Kafka: asynchronous CDC pipeline from Oracle.
- KafkaConsumer: applies non-`BIAN_API` booking deltas into Valkey for near-real-time balance.
- Valkey: low-latency balance serving store used by `GetBalance`.
