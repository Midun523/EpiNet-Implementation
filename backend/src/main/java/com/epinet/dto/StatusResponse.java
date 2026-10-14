package com.epinet.dto;

import java.time.LocalDateTime;

/**
 * DTO for /api/analyses/{id}/status response.
 */
public record StatusResponse(
        String analysisId,
        String status,
        String filename,
        LocalDateTime createdAt,
        String errorMessage
) {}
