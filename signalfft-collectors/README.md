# signalfft-collectors

Lambda functions that ingest public data from SEC EDGAR, news, and social sources. Each collector runs on a schedule via EventBridge, fetches data from external APIs, deduplicates via content hashing, stores raw artifacts immutably in S3, and publishes RawEventCollected events to SQS for downstream feature extraction. Collectors operate in the Intelligence plane and use the role_intelligence IAM role.
