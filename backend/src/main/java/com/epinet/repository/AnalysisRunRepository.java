package com.epinet.repository;

import com.epinet.model.AnalysisRun;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Spring Data JPA repository for AnalysisRun.
 */
@Repository
public interface AnalysisRunRepository extends JpaRepository<AnalysisRun, String> {

    /** Returns all runs ordered newest-first for the history page. */
    List<AnalysisRun> findAllByOrderByCreatedAtDesc();

    /**
     * WHY countByStatus: Used by Micrometer Prometheus gauge to observe
     * the count of jobs in each lifecycle status directly from the database.
     */
    long countByStatus(AnalysisRun.Status status);

    /**
     * WHY updateStatusIf: Targeted single-column UPDATE. Transitions status without
     * loading or rewriting the 40 MB csv_data blob.
     */
    @Modifying(clearAutomatically = true)
    @Transactional
    @Query("UPDATE AnalysisRun a SET a.status = :status WHERE a.id = :id AND a.status = :expectedStatus")
    int updateStatusIf(
            @Param("id") String id,
            @Param("status") AnalysisRun.Status status,
            @Param("expectedStatus") AnalysisRun.Status expectedStatus
    );

    /**
     * WHY findCsvDataById: Streams ONLY the csvData blob directly without loading
     * or managing the entire AnalysisRun entity into the Hibernate session.
     */
    @Query("SELECT a.csvData FROM AnalysisRun a WHERE a.id = :id")
    byte[] findCsvDataById(@Param("id") String id);

    /**
     * WHY markCompleted: Targeted UPDATE for terminal status. Sets status, resultJson,
     * completedAt, and clears csvData (sets NULL) in a single query without rewriting the blob.
     * WHY status guard: returns 0 if the job is already COMPLETED or FAILED, making the
     * query idempotent at the SQL level (not just the consumer level).
     */
    @Modifying(clearAutomatically = true)
    @Transactional
    @Query("UPDATE AnalysisRun a SET a.status = com.epinet.model.AnalysisRun$Status.COMPLETED, a.resultJson = :resultJson, a.completedAt = :completedAt, a.csvData = NULL, a.errorMessage = NULL WHERE a.id = :id AND a.status IN (com.epinet.model.AnalysisRun$Status.PENDING, com.epinet.model.AnalysisRun$Status.PROCESSING)")
    int markCompleted(
            @Param("id") String id,
            @Param("resultJson") String resultJson,
            @Param("completedAt") LocalDateTime completedAt
    );

    /**
     * WHY markFailed: Targeted UPDATE for failed status. Sets status, errorMessage,
     * completedAt, and clears csvData in a single query without rewriting the blob.
     * WHY status guard: returns 0 if the job is already COMPLETED or FAILED, making the
     * query idempotent at the SQL level (not just the consumer level).
     */
    @Modifying(clearAutomatically = true)
    @Transactional
    @Query("UPDATE AnalysisRun a SET a.status = com.epinet.model.AnalysisRun$Status.FAILED, a.errorMessage = :errorMessage, a.completedAt = :completedAt, a.csvData = NULL WHERE a.id = :id AND a.status IN (com.epinet.model.AnalysisRun$Status.PENDING, com.epinet.model.AnalysisRun$Status.PROCESSING)")
    int markFailed(
            @Param("id") String id,
            @Param("errorMessage") String errorMessage,
            @Param("completedAt") LocalDateTime completedAt
    );

    /**
     * WHY findStuckProcessingJobs: Queries jobs remaining in PROCESSING status created prior to cutoff time.
     */
    @Query("SELECT a FROM AnalysisRun a WHERE a.status = com.epinet.model.AnalysisRun$Status.PROCESSING AND a.createdAt < :cutoff")
    List<AnalysisRun> findStuckProcessingJobs(@Param("cutoff") LocalDateTime cutoff);
}
