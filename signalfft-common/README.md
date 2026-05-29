# signalfft-common

Shared models, enums, event schemas, and DynamoDB helpers used by all SignalFFT services. This package defines the canonical data models (entities, events, features, signals, waves, narratives, attention fields, trade candidates, and outcomes), enumeration types, inter-service event schemas for SQS/EventBridge messaging, and typed DynamoDB client wrappers with key builders. All other SignalFFT services depend on this package.
