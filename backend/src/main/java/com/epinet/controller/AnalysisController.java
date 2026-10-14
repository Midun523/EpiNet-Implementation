package com.epinet.controller;

import com.epinet.dto.AnalysisSummary;
import com.epinet.dto.StatusResponse;
import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import com.epinet.service.AnalysisService;
import com.epinet.service.CsvValidator;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * REST controller exposing the four analysis endpoints.
 * CORS is globally configured in {@link com.epinet.config.CorsConfig}.
 */
@RestController
@RequestMapping("/api")
public class AnalysisController {

    private final AnalysisRunRepository repo;
    private final AnalysisService analysisService;
    private final ObjectMapper mapper;

    public AnalysisController(
            AnalysisRunRepository repo,
            AnalysisService analysisService,
            ObjectMapper mapper
    ) {
        this.repo = repo;
        this.analysisService = analysisService;
        this.mapper = mapper;
    }

    // ── POST /api/uploads ─────────────────────────────────────────────────────

    @PostMapping("/uploads")
    public ResponseEntity<?> upload(@RequestParam("file") MultipartFile file) throws IOException {

        // Validate CSV structure and encoding; throws ValidationException on failure
        byte[] bytes = CsvValidator.validate(file);

        String analysisId = UUID.randomUUID().toString();
        AnalysisRun run = new AnalysisRun(analysisId, file.getOriginalFilename());
        repo.save(run);

        // Fire-and-forget; response returns before background processing completes
        analysisService.runAnalysis(analysisId, bytes, file.getOriginalFilename());

        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .body(Map.of("analysisId", analysisId, "status", "PROCESSING"));
    }

    // ── GET /api/analyses/{id}/status ─────────────────────────────────────────

    @GetMapping("/analyses/{id}/status")
    public ResponseEntity<StatusResponse> getStatus(@PathVariable String id) {
        return repo.findById(id)
                .map(r -> ResponseEntity.ok(new StatusResponse(
                        r.getId(), r.getStatus().name(), r.getFilename(),
                        r.getCreatedAt(), r.getErrorMessage())))
                .orElse(ResponseEntity.notFound().build());
    }

    // ── GET /api/analyses/{id}/result ─────────────────────────────────────────

    @GetMapping("/analyses/{id}/result")
    public ResponseEntity<?> getResult(@PathVariable String id) {
        return repo.findById(id)
                .map(r -> {
                    if (r.getStatus() != AnalysisRun.Status.COMPLETED) {
                        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                                .<Object>body(Map.of("error", "Result not ready. Status: " + r.getStatus()));
                    }
                    try {
                        // Return parsed JSON object rather than double-encoded string
                        Map<String, Object> result = mapper.readValue(
                                r.getResultJson(), new TypeReference<>() {});
                        return ResponseEntity.ok(result);
                    } catch (Exception e) {
                        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                                .<Object>body(Map.of("error", "Failed to parse stored result"));
                    }
                })
                .orElse(ResponseEntity.notFound().build());
    }

    // ── GET /api/analyses (history) ───────────────────────────────────────────

    @GetMapping("/analyses")
    public List<AnalysisSummary> listAnalyses() {
        return repo.findAllByOrderByCreatedAtDesc().stream()
                .map(r -> new AnalysisSummary(
                        r.getId(), r.getFilename(), r.getStatus().name(), r.getCreatedAt()))
                .collect(Collectors.toList());
    }
}
