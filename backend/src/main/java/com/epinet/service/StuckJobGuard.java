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
 * Scheduled background task that identifies and recovers jobs stuck in PROCESSING state.
 *
 * WHY: If an ML worker crashes mid-computation before completing or publishing failure,
 * the job would otherwise remain in PROCESSING indefinitely. This guard transitions such jobs
 * to FAILED after a configurable timeout and records an observability metric.
 */
@Component
@ConditionalOnProperty(name = "analysis.stuck-job.enabled", havingValue = "true", matchIfMissing = true)
public class StuckJobGuard {

    private static final Logger log = LoggerFactory.getLogger(StuckJobGuard.class);

    private final AnalysisRunRepository repository;
    private final AnalysisMetrics metrics;
    private final long timeoutSeconds;

    public StuckJobGuard(
            AnalysisRunRepository repository,
            AnalysisMetrics metrics,
            @Value("${analysis.stuck-job.timeout-seconds:300}") long timeoutSeconds
    ) {
        this.repository = repository;
        this.metrics = metrics;
        this.timeoutSeconds = timeoutSeconds;
    }

    @Scheduled(fixedDelayString = "${analysis.stuck-job.check-interval-ms:30000}")
    public void checkStuckJobs() {
        LocalDateTime cutoff = LocalDateTime.now().minusSeconds(timeoutSeconds);
        List<AnalysisRun> stuckJobs = repository.findStuckProcessingJobs(cutoff);
        if (stuckJobs.isEmpty()) {
            return;
        }

        log.warn("Found {} stuck PROCESSING jobs older than {}s (cutoff: {})", stuckJobs.size(), timeoutSeconds, cutoff);
        for (AnalysisRun job : stuckJobs) {
            String errorMsg = "Job timed out after " + timeoutSeconds + "s in PROCESSING state (stuck job guard)";
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
