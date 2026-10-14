package com.epinet;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableAsync;

/**
 * Entry point for the EpiNet Visualizer backend.
 * @EnableAsync enables background analysis processing via @Async.
 */
@SpringBootApplication
@EnableAsync
public class EpiNetApplication {

    public static void main(String[] args) {
        SpringApplication.run(EpiNetApplication.class, args);
    }
}
