package com.epinet.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;

/**
 * Thread pool configuration for @Async analysis tasks.
 * Explicitly wires 'analysisExecutor' for background ML execution.
 *
 * Sizing notes:
 * - corePoolSize(2): baseline concurrency for typical workloads.
 * - queueCapacity(10): keeps queue bounded so burst uploads prompt executor
 *   expansion up to maxPoolSize(5).
 * - maxPoolSize(5): bounds concurrent outbound calls so the ML service isn't overwhelmed.
 */
@Configuration
@EnableAsync
public class AsyncConfig {

    @Bean(name = "analysisExecutor")
    public Executor analysisExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(2);
        executor.setMaxPoolSize(5);
        executor.setQueueCapacity(10);
        executor.setThreadNamePrefix("analysis-worker-");
        executor.initialize();
        return executor;
    }
}
