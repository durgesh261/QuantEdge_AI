package com.quantedge.common.exception;

import java.time.Instant;
import java.util.Map;

public class ErrorResponse {
    private Instant timestamp;
    private int status;
    private String error;
    private String message;
    private Map<String, String> validationErrors;

    public ErrorResponse() {}

    public ErrorResponse(Instant timestamp, int status, String error, String message, Map<String, String> validationErrors) {
        this.timestamp = timestamp;
        this.status = status;
        this.error = error;
        this.message = message;
        this.validationErrors = validationErrors;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private Instant timestamp;
        private int status;
        private String error;
        private String message;
        private Map<String, String> validationErrors;

        public Builder timestamp(Instant timestamp) { this.timestamp = timestamp; return this; }
        public Builder status(int status) { this.status = status; return this; }
        public Builder error(String error) { this.error = error; return this; }
        public Builder message(String message) { this.message = message; return this; }
        public Builder validationErrors(Map<String, String> validationErrors) { this.validationErrors = validationErrors; return this; }

        public ErrorResponse build() {
            return new ErrorResponse(timestamp, status, error, message, validationErrors);
        }
    }

    public Instant getTimestamp() { return timestamp; }
    public void setTimestamp(Instant timestamp) { this.timestamp = timestamp; }

    public int getStatus() { return status; }
    public void setStatus(int status) { this.status = status; }

    public String getError() { return error; }
    public void setError(String error) { this.error = error; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public Map<String, String> getValidationErrors() { return validationErrors; }
    public void setValidationErrors(Map<String, String> validationErrors) { this.validationErrors = validationErrors; }
}