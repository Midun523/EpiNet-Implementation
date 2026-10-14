package com.epinet.dto;

import java.time.LocalDateTime;

/**
 * DTO for the analyses list (history) endpoint.
 */
public record AnalysisSummary(
        String id,
        String filename,
        String status,
        LocalDateTime createdAt
) {}
