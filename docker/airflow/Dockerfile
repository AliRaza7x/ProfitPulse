# Pipeline image: Airflow + PySpark (local mode) + Kafka client.
# One image serves the Airflow services and the `tools` service (CLI, tests, producer).
FROM apache/airflow:3.3.2-python3.12

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends default-jre-headless curl procps \
 && rm -rf /var/lib/apt/lists/*

ARG SPARK_VERSION=4.0.4
ARG KAFKA_CLIENTS_VERSION=3.9.1
ARG MAVEN=https://repo1.maven.org/maven2

# Spark connector jars (Kafka source, PostgreSQL JDBC). Baked in so runs are offline-safe.
RUN mkdir -p /opt/spark-extra-jars \
 && cd /opt/spark-extra-jars \
 && curl -fsSLO ${MAVEN}/org/apache/spark/spark-sql-kafka-0-10_2.13/${SPARK_VERSION}/spark-sql-kafka-0-10_2.13-${SPARK_VERSION}.jar \
 && curl -fsSLO ${MAVEN}/org/apache/spark/spark-token-provider-kafka-0-10_2.13/${SPARK_VERSION}/spark-token-provider-kafka-0-10_2.13-${SPARK_VERSION}.jar \
 && curl -fsSLO ${MAVEN}/org/apache/kafka/kafka-clients/${KAFKA_CLIENTS_VERSION}/kafka-clients-${KAFKA_CLIENTS_VERSION}.jar \
 && curl -fsSLO ${MAVEN}/org/apache/commons/commons-pool2/2.12.0/commons-pool2-2.12.0.jar \
 && curl -fsSLO ${MAVEN}/org/postgresql/postgresql/42.7.7/postgresql-42.7.7.jar \
 && chmod -R a+r /opt/spark-extra-jars

# Work directories created here so named volumes inherit airflow ownership.
RUN mkdir -p /opt/profitpulse/lake /opt/profitpulse/reports \
 && chown -R airflow:root /opt/profitpulse

USER airflow
COPY --chown=airflow:root pyproject.toml /opt/profitpulse/pyproject.toml
RUN pip install --no-cache-dir \
      -c "https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt" \
      "confluent-kafka>=2.5" "pyspark==${SPARK_VERSION}" "pandas>=2.2" "numpy>=1.26" "pyarrow>=15" \
      "pyyaml>=6" "psycopg2-binary>=2.9" "pytest>=8" "pytest-cov>=5"

WORKDIR /opt/profitpulse
ENV PYTHONPATH=/opt/profitpulse/src \
    PP_EXTRA_JARS_DIR=/opt/spark-extra-jars \
    PP_LAKE_DIR=/opt/profitpulse/lake \
    PP_CONFIG_DIR=/opt/profitpulse/config \
    PP_DATA_DIR=/opt/profitpulse/data
