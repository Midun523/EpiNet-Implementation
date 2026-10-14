package com.epinet.kafka;

/**
 * WHY THIS CLASS EXISTS:
 * This is the event published to the "analysis.requested" Kafka topic when a
 * user uploads a CSV. It intentionally carries ONLY the analysis ID and filename
 * — NOT the CSV data — because Kafka's default message limit is ~1 MB. The CSV
 * bytes are stored in MySQL and the ML service fetches them via a REST endpoint.
 *
 * The analysisId is also used as the Kafka message key so that all events for
 * the same job land on the same partition (ordering guarantee within a job).
 */
public class AnalysisRequestedEvent {

    private String analysisId;
    private String filename;

    public AnalysisRequestedEvent() {} // Jackson needs a no-arg constructor

    public AnalysisRequestedEvent(String analysisId, String filename) {
        this.analysisId = analysisId;
        this.filename = filename;
    }

    public String getAnalysisId() { return analysisId; }
    public void setAnalysisId(String analysisId) { this.analysisId = analysisId; }

    public String getFilename() { return filename; }
    public void setFilename(String filename) { this.filename = filename; }
}
