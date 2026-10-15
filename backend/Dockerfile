# backend/Dockerfile — Multi-stage container build for EpiNet Spring Boot backend
# Stage 1: Build the application artifact with Maven
# WHY maven:3.9.9-eclipse-temurin-21: official Eclipse Temurin JDK 21 image matching Java 21 target
FROM maven:3.9.9-eclipse-temurin-21 AS builder
WORKDIR /build

# WHY copy pom.xml first: caches dependency downloads in Docker layer cache unless pom.xml changes
COPY pom.xml .
RUN mvn dependency:go-offline -B

# Copy source and package jar skipping tests (tests are verified in dev/CI)
COPY src ./src
RUN mvn clean package -DskipTests -B

# Stage 2: Minimal runtime environment
# WHY eclipse-temurin:21-jre-jammy: minimal attack surface and small footprint for Java 21
FROM eclipse-temurin:21-jre-jammy
WORKDIR /app

# WHY curl: required for Docker container healthcheck against /actuator/health
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# WHY non-root user: security best practice to prevent privilege escalation
RUN groupadd -g 10001 epinet && \
    useradd -u 10001 -g epinet -m -s /bin/bash epinet

# Copy the built jar from the builder stage
COPY --from=builder /build/target/epinet-backend-1.0.0.jar app.jar
RUN chown -R epinet:epinet /app

USER epinet

# WHY JVM flags: -XX:MaxRAMPercentage=75.0 ensures the JVM heap respects cgroup memory limits
# (e.g., 750 MB heap in a 1 GB container), avoiding Linux kernel OOM killer terminations.
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75.0 -XX:+UseG1GC"

EXPOSE 8080

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar app.jar"]
