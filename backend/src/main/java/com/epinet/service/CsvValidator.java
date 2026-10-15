package com.epinet.service;

import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;

/**
 * Static validator for uploaded pharmacogenomic CSV files.
 * Verifies file extension, size boundary, UTF-8 encoding (handling BOMs),
 * and required metadata & gene header columns.
 */
public final class CsvValidator {

    private static final long MAX_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB

    private static final List<String> REQUIRED_GENE_COLUMNS = Arrays.asList(
            "CYP2D6", "CYP2C19", "CYP3A4", "CYP2C9",
            "VKORC1", "SLCO1B1", "TPMT", "DPYD", "UGT1A1", "ABCB1"
    );

    private CsvValidator() {}

    /**
     * Validates the multipart file and returns its raw bytes.
     *
     * @param file uploaded multipart file
     * @return raw byte array of the valid CSV
     * @throws ValidationException on any validation rule violation
     * @throws IOException on I/O read failure
     */
    public static byte[] validate(MultipartFile file) throws IOException {
        String name = file.getOriginalFilename() == null ? "" : file.getOriginalFilename();

        if (!name.toLowerCase().endsWith(".csv")) {
            throw new ValidationException("Only .csv files are accepted. Received: " + name);
        }

        if (file.getSize() > MAX_SIZE_BYTES) {
            throw new ValidationException(
                    "File size " + file.getSize() / 1024 + " KB exceeds the 50 MB limit.");
        }

        if (file.isEmpty()) {
            throw new ValidationException("Uploaded file is empty.");
        }

        String headerLine;
        try (java.io.BufferedReader reader = new java.io.BufferedReader(
                new java.io.InputStreamReader(file.getInputStream(), StandardCharsets.UTF_8))) {
            headerLine = reader.readLine();
        }
        if (headerLine == null || headerLine.isBlank()) {
            throw new ValidationException("CSV is missing a header row.");
        }
        // Strip UTF-8 Byte Order Mark (BOM) if exported by Excel
        if (headerLine.startsWith("\uFEFF")) {
            headerLine = headerLine.substring(1);
        }

        List<String> headers = Arrays.stream(headerLine.split(","))
                .map(String::trim)
                .toList();

        if (!headers.contains("sample_id")) {
            throw new ValidationException("CSV must have a 'sample_id' column.");
        }
        if (!headers.contains("phenotype")) {
            throw new ValidationException("CSV must have a 'phenotype' column.");
        }

        List<String> missingGenes = REQUIRED_GENE_COLUMNS.stream()
                .filter(g -> !headers.contains(g))
                .toList();
        if (!missingGenes.isEmpty()) {
            throw new ValidationException("CSV is missing gene columns: " + missingGenes);
        }

        return file.getBytes();
    }

    /** Unchecked domain exception thrown when CSV content violates structure or schema rules. */
    public static class ValidationException extends RuntimeException {
        public ValidationException(String message) {
            super(message);
        }
    }
}
