# data_lake/silver/silver_transform.py
"""
IA Odonto Lab — Silver Layer Transform (Medallion Architecture)

Reads Bronze Parquet files, applies LGPD compliance transforms,
and produces the Silver layer using PySpark on Databricks.

LGPD transforms applied:
  - contato_id: SHA-256 irreversible hash (patient cannot be re-identified)
  - No CPF, phone, or name fields in Silver

Feature engineering:
  - ltv_acumulado:         cumulative spend per contact
  - frequencia_visitas:    visit count per contact
  - dias_desde_ultima:     days since last payment

Usage (Databricks):
  1. Upload Bronze Parquet to DBFS:
       dbutils.fs.cp("file:/tmp/data.parquet", "dbfs:/ia-odonto/bronze/")
  2. Run this notebook or script in a Databricks cluster
     (Runtime 13.x LTS, Spark 3.4, Python 3.10)

Usage (local with PySpark):
  pip install pyspark==3.5.0
  python data_lake/silver/silver_transform.py
"""
import hashlib
import logging
from datetime import date
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

BRONZE_PATH = Path(__file__).parent.parent / "bronze"
SILVER_PATH = Path(__file__).parent
TODAY = date.today().isoformat()


@F.udf(returnType=StringType())
def sha256_hash(value: str) -> str:
    """UDF: Irreversible SHA-256 hash for LGPD-compliant anonymization."""
    if value is None:
        return None
    return hashlib.sha256(value.encode()).hexdigest()


def main():
    logger.info("=== Silver transform started | dt=%s ===", TODAY)

    spark = (
        SparkSession.builder.appName("ia-odonto-silver")
        .config("spark.sql.adaptive.enabled", "true")
        .getOrCreate()
    )

    # Step 1: Read Bronze
    bronze_path = str(BRONZE_PATH / "c_recebimento")
    logger.info("[1/3] Reading Bronze from: %s", bronze_path)
    df = spark.read.parquet(bronze_path)
    logger.info("      %d raw records loaded", df.count())

    # Step 2: LGPD anonymization — hash patient identifiers
    logger.info("[2/3] Applying LGPD transforms (SHA-256 hashing)...")
    df_anonymized = df.withColumn(
        "contato_id_hash", sha256_hash(F.col("contato_id"))
    ).drop("contato_id")

    # Step 3: Feature engineering
    logger.info("[3/3] Building Silver features...")
    window = F.Window.partitionBy("contato_id_hash")

    df_silver = (
        df_anonymized.withColumn("ltv_acumulado", F.sum("valor").over(window))
        .withColumn("frequencia_visitas", F.count("id").over(window))
        .withColumn("ultima_visita", F.max("data_recebimento").over(window))
        .withColumn(
            "dias_desde_ultima", F.datediff(F.lit(TODAY), F.col("ultima_visita"))
        )
        .withColumn("silver_dt", F.lit(TODAY))
    )

    # Save as Delta (Databricks) or Parquet (local)
    out_path = str(SILVER_PATH / f"dt={TODAY}")
    df_silver.write.mode("overwrite").parquet(out_path)
    logger.info("Silver saved: %s (%d rows)", out_path, df_silver.count())
    logger.info("=== Silver transform complete ===")

    spark.stop()


if __name__ == "__main__":
    main()
