import logging


class MySQLQueryClient:
    def __init__(self, host: str, user: str, password: str, database: str):
        self.host = host
        self.user = user
        self.password = password
        self.database = database

    def fetch_query_results(self, query, params=None, return_header=False):
        """
        Connects to a MySQL database, executes a SELECT query, and returns the results as a list of lists.
        Args:
            query (str): The SELECT query to execute. Use %s placeholders for parameters.
            params (tuple|list|dict|None): Parameters to bind to the query. Pass user input
                via this argument rather than f-strings to avoid SQL injection.
            return_header (bool): If True, returns (header, rows) where header is the list of column names.
        Returns:
            list[list] or (list, list): Query results as a list of rows, each row is a list of column values.
            If return_header is True, returns (header, rows).
        Raises:
            Exception: If connection or query fails.
        """
        import pymysql

        connection = None
        cursor = None
        try:
            connection = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
            )
            cursor = connection.cursor()
            if params is not None:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            results = cursor.fetchall()
            header = [desc[0] for desc in cursor.description] if return_header else None
            rows = [list(row) for row in results]
            if return_header:
                return header, rows
            else:
                return rows
        except Exception as e:
            logging.error(f"MySQL query failed: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()


if __name__ == "__main__":
    from pathlib import Path
    import yaml

    creds_path = Path(__file__).parent / ".DBCreds.yaml"
    with open(creds_path) as f:
        c = yaml.safe_load(f)["crypto_db"]

    client = MySQLQueryClient(host=c["host"], user=c["user"], password=c["password"], database=c["database"])
    query = "SELECT service_id, hostname, cover_names FROM fh_config WHERE hostname LIKE %s"
    rows = client.fetch_query_results(query, params=("%TA-TKY-A-63%",))
    FH_dict = {}
    for row in rows:
        service_id, hostname, cover_names = row
        FH_dict[service_id] = {
            'service_id': service_id,
            'cover_names': cover_names.split(',') if cover_names else [],
        }
    for key, value in FH_dict.items():
        print(f"Service ID: {value['service_id']}, Cover Names: {value['cover_names']}")
