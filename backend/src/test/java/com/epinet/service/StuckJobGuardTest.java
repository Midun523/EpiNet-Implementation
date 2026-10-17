package com.epinet.service;

import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Unit test for StuckJobGuard.
 * WHY: Verifies that stuck PROCESSING jobs are transitioned to FAILED and metrics are incremented.
 */
class StuckJobGuardTest {

    @Test
    @DisplayName("checkStuckJobs marks stuck jobs as FAILED and records metric")
    void checkStuckJobs_recoversStuckJobs() {
        AnalysisRunRepository repo = Mockito.mock(AnalysisRunRepository.class);
        AnalysisMetrics metrics = Mockito.mock(AnalysisMetrics.class);

        AnalysisRun stuckJob = new AnalysisRun("stuck-123", "data.csv");
        stuckJob.setStatus(AnalysisRun.Status.PROCESSING);
        stuckJob.setCreatedAt(LocalDateTime.now().minusMinutes(10));

        when(repo.findStuckProcessingJobs(any(LocalDateTime.class))).thenReturn(List.of(stuckJob));
        when(repo.markFailed(eq("stuck-123"), contains("stuck job guard"), any(LocalDateTime.class))).thenReturn(1);

        StuckJobGuard guard = new StuckJobGuard(repo, metrics, 300);
        guard.checkStuckJobs();

        verify(repo, times(1)).markFailed(eq("stuck-123"), contains("stuck job guard"), any(LocalDateTime.class));
        verify(metrics, times(1)).recordStuckJobTimedOut();
    }

    @Test
    @DisplayName("checkStuckJobs does nothing when no stuck jobs exist")
    void checkStuckJobs_emptyList_noActions() {
        AnalysisRunRepository repo = Mockito.mock(AnalysisRunRepository.class);
        AnalysisMetrics metrics = Mockito.mock(AnalysisMetrics.class);

        when(repo.findStuckProcessingJobs(any(LocalDateTime.class))).thenReturn(List.of());

        StuckJobGuard guard = new StuckJobGuard(repo, metrics, 300);
        guard.checkStuckJobs();

        verify(repo, never()).markFailed(any(), any(), any());
        verify(metrics, never()).recordStuckJobTimedOut();
    }
}
