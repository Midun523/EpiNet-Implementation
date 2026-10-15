package com.epinet.repository;

import com.epinet.model.AnalysisRun;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit test verifying idempotency of targeted markCompleted and markFailed queries.
 * WHY: Calling markCompleted or markFailed on an already-terminal job (COMPLETED or FAILED)
 * must be a no-op (returning 0 updated rows and leaving existing data intact).
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class AnalysisRunRepositoryTest {

    @Autowired
    private AnalysisRunRepository repo;

    private String pendingId;
    private String processingId;

    @BeforeEach
    void setUp() {
        repo.deleteAll();

        AnalysisRun pending = new AnalysisRun("job-pending", "sample.csv");
        pending.setStatus(AnalysisRun.Status.PENDING);
        pending.setCsvData("col1,col2\n1,2".getBytes());
        repo.saveAndFlush(pending);
        pendingId = pending.getId();

        AnalysisRun processing = new AnalysisRun("job-processing", "sample.csv");
        processing.setStatus(AnalysisRun.Status.PROCESSING);
        processing.setCsvData("col1,col2\n3,4".getBytes());
        repo.saveAndFlush(processing);
        processingId = processing.getId();
    }

    @Test
    @DisplayName("markCompleted transitions PENDING job and clears csvData")
    void markCompleted_onPending_succeeds() {
        int updated = repo.markCompleted(pendingId, "{\"result\": 1}", LocalDateTime.now());
        assertThat(updated).isEqualTo(1);

        AnalysisRun run = repo.findById(pendingId).orElseThrow();
        assertThat(run.getStatus()).isEqualTo(AnalysisRun.Status.COMPLETED);
        assertThat(run.getResultJson()).isEqualTo("{\"result\": 1}");
        assertThat(run.getCsvData()).isNull();
    }

    @Test
    @DisplayName("markCompleted on already COMPLETED job is a no-op (idempotent)")
    void markCompleted_onAlreadyCompleted_returnsZero() {
        // First completion
        repo.markCompleted(pendingId, "{\"run\": 1}", LocalDateTime.now());

        // Second completion attempt
        int secondUpdate = repo.markCompleted(pendingId, "{\"run\": 2}", LocalDateTime.now());
        assertThat(secondUpdate).isEqualTo(0);

        // State remains as from first completion
        AnalysisRun run = repo.findById(pendingId).orElseThrow();
        assertThat(run.getStatus()).isEqualTo(AnalysisRun.Status.COMPLETED);
        assertThat(run.getResultJson()).isEqualTo("{\"run\": 1}");
    }

    @Test
    @DisplayName("markCompleted on already FAILED job is a no-op (idempotent)")
    void markCompleted_onAlreadyFailed_returnsZero() {
        repo.markFailed(pendingId, "Initial error", LocalDateTime.now());

        int completedUpdate = repo.markCompleted(pendingId, "{\"run\": 1}", LocalDateTime.now());
        assertThat(completedUpdate).isEqualTo(0);

        AnalysisRun run = repo.findById(pendingId).orElseThrow();
        assertThat(run.getStatus()).isEqualTo(AnalysisRun.Status.FAILED);
        assertThat(run.getErrorMessage()).isEqualTo("Initial error");
    }

    @Test
    @DisplayName("markFailed transitions PROCESSING job and clears csvData")
    void markFailed_onProcessing_succeeds() {
        int updated = repo.markFailed(processingId, "Worker crash", LocalDateTime.now());
        assertThat(updated).isEqualTo(1);

        AnalysisRun run = repo.findById(processingId).orElseThrow();
        assertThat(run.getStatus()).isEqualTo(AnalysisRun.Status.FAILED);
        assertThat(run.getErrorMessage()).isEqualTo("Worker crash");
        assertThat(run.getCsvData()).isNull();
    }

    @Test
    @DisplayName("markFailed on already COMPLETED job is a no-op (idempotent)")
    void markFailed_onAlreadyCompleted_returnsZero() {
        repo.markCompleted(processingId, "{\"ok\": true}", LocalDateTime.now());

        int failedUpdate = repo.markFailed(processingId, "Late error", LocalDateTime.now());
        assertThat(failedUpdate).isEqualTo(0);

        AnalysisRun run = repo.findById(processingId).orElseThrow();
        assertThat(run.getStatus()).isEqualTo(AnalysisRun.Status.COMPLETED);
        assertThat(run.getResultJson()).isEqualTo("{\"ok\": true}");
    }

    @Test
    @DisplayName("markFailed on already FAILED job is a no-op (idempotent)")
    void markFailed_onAlreadyFailed_returnsZero() {
        repo.markFailed(processingId, "First failure", LocalDateTime.now());

        int secondFailure = repo.markFailed(processingId, "Second failure", LocalDateTime.now());
        assertThat(secondFailure).isEqualTo(0);

        AnalysisRun run = repo.findById(processingId).orElseThrow();
        assertThat(run.getStatus()).isEqualTo(AnalysisRun.Status.FAILED);
        assertThat(run.getErrorMessage()).isEqualTo("First failure");
    }
}
