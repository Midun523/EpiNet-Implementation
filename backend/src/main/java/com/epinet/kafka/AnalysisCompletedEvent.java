package com.epinet.kafka;

/**
 * WHY THIS CLASS EXISTS:
 * Payload published to the "analysis.completed" Kafka topic by the ML service
 * after it finishes (or fails) an analysis. The Spring Boot backend consumes
 * this to update the job row in MySQL.
 *
 * status: "COMPLETED" or "FAILED"
 * resultJson: the full ML output JSON (only when COMPLETED)
 * errorMessage: human-readable reason (only when FAILED)
 */
public class AnalysisCompletedEvent {

    private String analysisId;
    private String status;       // "COMPLETED" or "FAILED"
    private String resultJson;   // full ML result, null on failure
    private String errorMessage;  // null on success

    public AnalysisCompletedEvent() {}

    public AnalysisCompletedEvent(String analysisId, String status,
                                   String resultJson, String errorMessage) {
        this.analysisId = analysisId;
        this.status = status;
        this.resultJson = resultJson;
        this.errorMessage = errorMessage;
    }

    public String getAnalysisId() { return analysisId; }
    public void setAnalysisId(String analysisId) { this.analysisId = analysisId; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public String getResultJson() { return resultJson; }
    public void setResultJson(String resultJson) { this.resultJson = resultJson; }

    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }
}
