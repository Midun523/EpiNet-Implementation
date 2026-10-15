package com.epinet.repository;

import com.epinet.model.AnalysisRun;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

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
}
