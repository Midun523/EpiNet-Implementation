package com.epinet.service;

import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

/**
 * Scheduled background task that identifies and recovers stuck jobs.
 *
 * WHY: If an ML worker crashes mid-computation before completing or publishing failure,
 * the job would otherwise remain in PROCESSING indefinitely. This guard transitions such jobs
 * to FAILED after a configurable timeout (default 300s).
 *
 * WHY separate PENDING timeout: Healthy queued jobs waiting during an ML service backlog or outage
 * should not be failed prematurely. A separate, longer timeout (default 1800s / 30 min) guards against
 * permanently abandoned queue messages while allowing queue backlog absorption.
 */
@Component
@ConditionalOnProperty(name = "analysis.stuck-job.enabled", havingValue = "true", matchIfMissing = true)
public class StuckJobGuard {

    private static final Logger log = LoggerFactory.getLogger(StuckJobGuard.class);

    private final AnalysisRunRepository repository;
    private final AnalysisMetrics metrics;
    private final long processingTimeoutSeconds;
    private final long pendingTimeoutSeconds;

    public StuckJobGuard(
            AnalysisRunRepository repository,
            AnalysisMetrics metrics,
            @Value("${analysis.stuck-job.processing-timeout-seconds:${analysis.stuck-job.timeout-seconds:300}}") long processingTimeoutSeconds,
            @Value("${analysis.stuck-job.pending-timeout-seconds:1800}") long pendingTimeoutSeconds
    ) {
        this.repository = repository;
        this.metrics = metrics;
        this.processingTimeoutSeconds = processingTimeoutSeconds;
        this.pendingTimeoutSeconds = pendingTimeoutSeconds;
    }

    public long getProcessingTimeoutSeconds() {
        return processingTimeoutSeconds;
    }

    public long getPendingTimeoutSeconds() {
        return pendingTimeoutSeconds;
    }

    @Scheduled(fixedDelayString = "${analysis.stuck-job.check-interval-ms:30000}")
    public void checkStuckJobs() {
        LocalDateTime now = LocalDateTime.now();

        // 1. Check stuck PROCESSING jobs (short timeout, default 300s)
        LocalDateTime processingCutoff = now.minusSeconds(processingTimeoutSeconds);
        List<AnalysisRun> stuckProcessing = repository.findStuckProcessingJobs(processingCutoff);
        failStuckJobs(stuckProcessing, processingTimeoutSeconds, "PROCESSING");

        // 2. Check stuck PENDING jobs (longer timeout, default 1800s / 30m)
        LocalDateTime pendingCutoff = now.minusSeconds(pendingTimeoutSeconds);
        List<AnalysisRun> stuckPending = repository.findStuckPendingJobs(pendingCutoff);
        failStuckJobs(stuckPending, pendingTimeoutSeconds, "PENDING");
    }

    private void failStuckJobs(List<AnalysisRun> jobs, long timeoutSeconds, String stateName) {
        if (jobs.isEmpty()) {
            return;
        }

        log.warn("Found {} stuck {} jobs older than {}s", jobs.size(), stateName, timeoutSeconds);
        for (AnalysisRun job : jobs) {
            String errorMsg = "Job timed out after " + timeoutSeconds + "s in " + job.getStatus() + " state (stuck job guard)";
            LocalDateTime now = LocalDateTime.now();
            int updated = repository.markFailed(job.getId(), errorMsg, now);
            if (updated > 0) {
                metrics.recordStuckJobTimedOut();
                if (job.getCreatedAt() != null) {
                    metrics.recordJobDuration(Duration.between(job.getCreatedAt(), now), "FAILED");
                }
                log.warn("Marked stuck job {} as FAILED: {}", job.getId(), errorMsg);
            }
        }
    }
}
