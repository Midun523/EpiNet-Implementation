package com.epinet.model;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * JPA entity representing one pharmacogenomic analysis run.
 * Stores the full ML result as JSON for retrieval without re-running.
 */
@Entity
@Table(name = "analysis_run")
public class AnalysisRun {

    public enum Status {
        /** Kafka path: job created, event not yet consumed by ML service */
        PENDING,
        /** Sync path: file received, not yet sent to ML service */
        UPLOADED,
        /** ML service is running the analysis */
        PROCESSING,
        COMPLETED,
        FAILED
    }

    @Id
    @Column(length = 36, nullable = false)
    private String id;

    @Column(nullable = false)
    private String filename;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private Status status;

    /** Full ML JSON payload; stored as LONGTEXT in MySQL, CLOB in H2. */
    @Lob
    @Column(name = "result_json")
    private String resultJson;

    @Column(name = "error_message")
    private String errorMessage;

    /**
     * WHY: Kafka messages should be small (default limit ~1 MB). We store the
     * uploaded CSV here so the Kafka event only carries analysisId. The ML
     * service fetches the data via GET /api/internal/analyses/{id}/data.
     */
    @Lob
    @Column(name = "csv_data")
    private byte[] csvData;

    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt;

    @Column(name = "completed_at")
    private LocalDateTime completedAt;

    // ── Constructors ──────────────────────────────────────────────────────────

    public AnalysisRun() {}

    public AnalysisRun(String id, String filename) {
        this.id = id;
        this.filename = filename;
        this.status = Status.UPLOADED;
        this.createdAt = LocalDateTime.now();
    }

    // ── Getters / Setters ─────────────────────────────────────────────────────

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getFilename() { return filename; }
    public void setFilename(String filename) { this.filename = filename; }

    public Status getStatus() { return status; }
    public void setStatus(Status status) { this.status = status; }

    public String getResultJson() { return resultJson; }
    public void setResultJson(String resultJson) { this.resultJson = resultJson; }

    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }

    public LocalDateTime getCompletedAt() { return completedAt; }
    public void setCompletedAt(LocalDateTime completedAt) { this.completedAt = completedAt; }

    public byte[] getCsvData() { return csvData; }
    public void setCsvData(byte[] csvData) { this.csvData = csvData; }
}
