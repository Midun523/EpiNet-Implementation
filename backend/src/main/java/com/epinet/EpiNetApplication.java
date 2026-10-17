package com.epinet;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableAsync;

import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Entry point for the EpiNet Visualizer backend.
 * @EnableAsync enables background analysis processing via @Async.
 * @EnableScheduling enables periodic background checks (e.g. StuckJobGuard).
 */
@SpringBootApplication
@EnableAsync
@EnableScheduling
public class EpiNetApplication {

    public static void main(String[] args) {
        SpringApplication.run(EpiNetApplication.class, args);
    }
}
