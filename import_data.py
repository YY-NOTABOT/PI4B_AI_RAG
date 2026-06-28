import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

from sample_knowledge import SAMPLE_KNOWLEDGE


def main() -> None:
    load_dotenv()
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        session.execute_write(create_constraints)
        for item in SAMPLE_KNOWLEDGE:
            session.execute_write(upsert_item, item)
    driver.close()
    print("Neo4j sample medical knowledge imported successfully.")


def create_constraints(tx):
    tx.run("CREATE CONSTRAINT disease_name IF NOT EXISTS FOR (d:Disease) REQUIRE d.name IS UNIQUE")
    tx.run("CREATE CONSTRAINT symptom_name IF NOT EXISTS FOR (s:Symptom) REQUIRE s.name IS UNIQUE")
    tx.run("CREATE CONSTRAINT medicine_name IF NOT EXISTS FOR (m:Medicine) REQUIRE m.name IS UNIQUE")
    tx.run("CREATE CONSTRAINT department_name IF NOT EXISTS FOR (d:Department) REQUIRE d.name IS UNIQUE")


def upsert_item(tx, item):
    tx.run(
        """
        MERGE (d:Disease {name: $disease})
        SET d.description = $description
        WITH d
        UNWIND $symptoms AS symptom_name
            MERGE (s:Symptom {name: symptom_name})
            MERGE (d)-[:HAS_SYMPTOM]->(s)
        WITH d
        UNWIND $medicines AS medicine_name
            MERGE (m:Medicine {name: medicine_name})
            MERGE (d)-[:TREATED_BY]->(m)
        WITH d
        UNWIND $departments AS department_name
            MERGE (dept:Department {name: department_name})
            MERGE (d)-[:VISIT_DEPARTMENT]->(dept)
        """,
        disease=item["disease"],
        symptoms=item["symptoms"],
        medicines=item["medicines"],
        departments=item["departments"],
        description=item["description"],
    )


if __name__ == "__main__":
    main()
