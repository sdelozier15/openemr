"""
AMC checkbox — Option 1: direct database write.

The "Medication Reconciliation Performed?" checkbox is stored in the
amc_misc_data table, not in form_encounter and not exposed via any
OpenEMR REST or FHIR endpoint. The internal PHP functions amcAdd /
amcAddForce write to this table directly.

Source confirmed at:
  openemr/library/amc.php     — amcAddForce inserts into amc_misc_data
  openemr/sql/database.sql    — 'med_reconc_amc' is the registered rule ID
  openemr/interface/patient_file/encounter/forms.php
                               — amcCollect("med_reconc_amc", ...) reads it

OPTION 1 replicates exactly what amcAddForce does: a direct INSERT into
amc_misc_data. This is the same operation the OpenEMR UI performs internally.

To use:
  1. Add DB credentials to your .env (see .env.example)
  2. pip install aiomysql
  3. Call insert_amc_checkbox(pid, encounter_id) from the submit endpoint

OPTION 3 (preferred if available on your instance):
  Open the encounter in the demo, check the box manually, and watch the
  network tab in DevTools. If OpenEMR fires an AJAX call, replicate that
  instead — it avoids direct DB access. Update this file if you find it.
"""

import os
import logging

logger = logging.getLogger(__name__)

DB_HOST = os.getenv("OPENEMR_DB_HOST", "localhost")
DB_PORT = int(os.getenv("OPENEMR_DB_PORT", "3306"))
DB_NAME = os.getenv("OPENEMR_DB_NAME", "openemr")
DB_USER = os.getenv("OPENEMR_DB_USER", "openemr")
DB_PASS = os.getenv("OPENEMR_DB_PASS", "openemr")


async def insert_amc_checkbox(pid: str, encounter_id: str) -> bool:
    """
    Insert a completed amc_misc_data row for med_reconc_amc.

    Equivalent to OpenEMR's internal call:
        amcAddForce("med_reconc_amc", true, $pid, "form_encounter", $encounter_id)

    Which inserts:
        INSERT INTO amc_misc_data
          (amc_id, pid, map_category, map_id, date_created, date_completed)
        VALUES
          ('med_reconc_amc', {pid}, 'form_encounter', {encounter_id}, NOW(), NOW())

    Returns True on success, False on failure (non-fatal — note already written).
    """
    try:
        import aiomysql
    except ImportError:
        logger.warning(
            "aiomysql not installed — AMC checkbox skipped. "
            "Run: pip install aiomysql"
        )
        return False

    try:
        conn = await aiomysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            db=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
        )
        async with conn.cursor() as cur:
            # Use INSERT IGNORE so re-running submit doesn't create duplicates
            await cur.execute(
                """
                INSERT IGNORE INTO amc_misc_data
                  (amc_id, pid, map_category, map_id, date_created, date_completed)
                VALUES
                  (%s, %s, %s, %s, NOW(), NOW())
                """,
                ("med_reconc_amc", pid, "form_encounter", encounter_id),
            )
            await conn.commit()
        conn.close()
        return True

    except Exception as e:
        logger.warning(
            f"AMC checkbox insert failed: {e}. "
            "Check OPENEMR_DB_* env vars. Note was still written to chart."
        )
        return False
