# signalfft-execution

Order routing and broker adapter for SignalFFT. Receives approved trade candidates from the risk gateway via SQS, routes them to the configured broker API (paper-trade adapter initially), and records execution outcomes for feedback loop calibration. Operates in the Execution plane using the isolated role_execution IAM role with no access to intelligence or decision data.
