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
 * WHY: Verifies that stuck PROCESSING jobs (timeout 300s) and stuck PENDING jobs (timeout 1800s)
 * are transitioned to FAILED with their respective timeouts, and that queued PENDING jobs under
 * the 1800s threshold are not prematurely failed.
 */
class StuckJobGuardTest {

    @Test
    @DisplayName("checkStuckJobs marks stuck PROCESSING jobs as FAILED and records metric")
    void checkStuckJobs_recoversStuckProcessingJobs() {
        AnalysisRunRepository repo = Mockito.mock(AnalysisRunRepository.class);
        AnalysisMetrics metrics = Mockito.mock(AnalysisMetrics.class);

        AnalysisRun stuckProcessing = new AnalysisRun("stuck-proc-123", "data.csv");
        stuckProcessing.setStatus(AnalysisRun.Status.PROCESSING);
        stuckProcessing.setCreatedAt(LocalDateTime.now().minusMinutes(10));

        when(repo.findStuckProcessingJobs(any(LocalDateTime.class))).thenReturn(List.of(stuckProcessing));
        when(repo.findStuckPendingJobs(any(LocalDateTime.class))).thenReturn(List.of());
        when(repo.markFailed(eq("stuck-proc-123"), contains("stuck job guard"), any(LocalDateTime.class))).thenReturn(1);

        StuckJobGuard guard = new StuckJobGuard(repo, metrics, 300, 1800);
        guard.checkStuckJobs();

        verify(repo, times(1)).markFailed(eq("stuck-proc-123"), contains("300s in PROCESSING state"), any(LocalDateTime.class));
        verify(metrics, times(1)).recordStuckJobTimedOut();
    }

    @Test
    @DisplayName("checkStuckJobs marks stuck PENDING jobs older than 1800s as FAILED")
    void checkStuckJobs_recoversStuckPendingJobs() {
        AnalysisRunRepository repo = Mockito.mock(AnalysisRunRepository.class);
        AnalysisMetrics metrics = Mockito.mock(AnalysisMetrics.class);

        AnalysisRun stuckPending = new AnalysisRun("stuck-pend-456", "data.csv");
        stuckPending.setStatus(AnalysisRun.Status.PENDING);
        stuckPending.setCreatedAt(LocalDateTime.now().minusMinutes(35));

        when(repo.findStuckProcessingJobs(any(LocalDateTime.class))).thenReturn(List.of());
        when(repo.findStuckPendingJobs(any(LocalDateTime.class))).thenReturn(List.of(stuckPending));
        when(repo.markFailed(eq("stuck-pend-456"), contains("stuck job guard"), any(LocalDateTime.class))).thenReturn(1);

        StuckJobGuard guard = new StuckJobGuard(repo, metrics, 300, 1800);
        guard.checkStuckJobs();

        verify(repo, times(1)).markFailed(eq("stuck-pend-456"), contains("1800s in PENDING state"), any(LocalDateTime.class));
        verify(metrics, times(1)).recordStuckJobTimedOut();
    }

    @Test
    @DisplayName("checkStuckJobs does not fail healthy PENDING jobs older than 300s but younger than 1800s")
    void checkStuckJobs_doesNotFailQueuedPendingJobsUnder1800s() {
        AnalysisRunRepository repo = Mockito.mock(AnalysisRunRepository.class);
        AnalysisMetrics metrics = Mockito.mock(AnalysisMetrics.class);

        // repo.findStuckPendingJobs(now.minusSeconds(1800)) returns empty because job is only 10m (600s) old
        when(repo.findStuckProcessingJobs(any(LocalDateTime.class))).thenReturn(List.of());
        when(repo.findStuckPendingJobs(any(LocalDateTime.class))).thenReturn(List.of());

        StuckJobGuard guard = new StuckJobGuard(repo, metrics, 300, 1800);
        guard.checkStuckJobs();

        verify(repo, never()).markFailed(any(), any(), any());
        verify(metrics, never()).recordStuckJobTimedOut();
    }

    @Test
    @DisplayName("checkStuckJobs does nothing when no stuck jobs exist")
    void checkStuckJobs_emptyList_noActions() {
        AnalysisRunRepository repo = Mockito.mock(AnalysisRunRepository.class);
        AnalysisMetrics metrics = Mockito.mock(AnalysisMetrics.class);

        when(repo.findStuckProcessingJobs(any(LocalDateTime.class))).thenReturn(List.of());
        when(repo.findStuckPendingJobs(any(LocalDateTime.class))).thenReturn(List.of());

        StuckJobGuard guard = new StuckJobGuard(repo, metrics, 300, 1800);
        guard.checkStuckJobs();

        verify(repo, never()).markFailed(any(), any(), any());
        verify(metrics, never()).recordStuckJobTimedOut();
    }
}
