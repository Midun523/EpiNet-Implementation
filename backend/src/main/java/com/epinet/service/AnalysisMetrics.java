package com.epinet.service;

import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.time.Duration;

/**
 * Custom metrics service for EpiNet observability (Phase 2).
 *
 * WHY:
 * 1. Exposes real-time gauges of jobs in each lifecycle status (PENDING, PROCESSING,
 *    COMPLETED, FAILED) queried dynamically on scrape from the repository.
 * 2. Exposes a Micrometer Timer recording end-to-end analysis job duration
 *    from job creation to final completion or failure.
 * 3. Exposes a Counter tracking jobs recovered from stuck PROCESSING state.
 */
@Component
public class AnalysisMetrics {

    private final MeterRegistry meterRegistry;
    private final AnalysisRunRepository repository;
    private final Counter stuckJobsCounter;

    public AnalysisMetrics(MeterRegistry meterRegistry, AnalysisRunRepository repository) {
        this.meterRegistry = meterRegistry;
        this.repository = repository;
        this.stuckJobsCounter = Counter.builder("epinet.stuck.jobs.timed.out.total")
                .description("Total number of analysis jobs transitioned to FAILED due to stuck PROCESSING timeout")
                .register(meterRegistry);
    }

    @PostConstruct
    public void registerGauges() {
        // WHY dynamic gauges: queried against DB repository on every Prometheus scrape,
        // avoiding metric desynchronization across restarts or multi-pod environments.
        for (AnalysisRun.Status status : new AnalysisRun.Status[]{
                AnalysisRun.Status.PENDING,
                AnalysisRun.Status.PROCESSING,
                AnalysisRun.Status.COMPLETED,
                AnalysisRun.Status.FAILED
        }) {
            Gauge.builder("epinet.jobs.by.status", repository, repo -> repo.countByStatus(status))
                    .tag("status", status.name())
                    .description("Current count of analysis jobs in database by lifecycle status")
                    .register(meterRegistry);
        }
    }

    /**
     * Records end-to-end job duration (created_at -> completed_at) tagged by terminal status.
     *
     * @param duration elapsed time from job creation to terminal state
     * @param status final status ("COMPLETED" or "FAILED")
     */
    public void recordJobDuration(Duration duration, String status) {
        if (duration == null || duration.isNegative()) {
            return;
        }
        Timer.builder("epinet.job.duration")
                .description("End-to-end duration of analysis jobs from creation to terminal state")
                .tag("status", status)
                .register(meterRegistry)
                .record(duration);
    }

    /**
     * Records a job timeout by incrementing the epinet.stuck.jobs.timed.out.total counter.
     */
    public void recordStuckJobTimedOut() {
        stuckJobsCounter.increment();
    }
}
