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
}
