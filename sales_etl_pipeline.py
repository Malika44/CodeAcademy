from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from sklearn.impute import SimpleImputer
import pandas as pd
import logging
import os
import pymysql
from pathlib import Path

# Configuration - Paths relative to DAG folder
DAG_FOLDER = os.path.dirname(__file__)
CSV_PATH = os.path.join(DAG_FOLDER, 'sales_data_sample.csv')
TRANSFORMED_DATA_PATH = os.path.join(DAG_FOLDER, 'files/transformed_data.csv')
MYSQL_TABLE = 'sales_data'

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2023, 1, 1),
    'retries': 2,
    'retry_delay': timedelta(minutes=3),
}

def create_table():
    """Create the MySQL table if it doesn't exist"""
    try:
        connection = pymysql.connect(
            host='host.docker.internal',
            user='root',
            password='Ibrahim2001@',
            database='mydatabase'
        )
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {MYSQL_TABLE} (
                    OrderID INT PRIMARY KEY,
                    OrderDate DATETIME,
                    ProductSerial VARCHAR(50),
                    Category VARCHAR(100),
                    Region VARCHAR(50),
                    QuantityInStock FLOAT,
                    Price FLOAT,
                    Sales FLOAT
                );
            """)
            connection.commit()
            logging.info(f"Table {MYSQL_TABLE} created or already exists")
            
    except Exception as e:
        logging.error(f"Table creation failed: {str(e)}")
        raise
    finally:
        connection.close()

def extract():
    """Extract data from CSV in DAG folder"""
    try:
        # Ensure the files directory exists
        os.makedirs(os.path.dirname(TRANSFORMED_DATA_PATH), exist_ok=True)
        
        df = pd.read_csv(CSV_PATH, encoding='ISO-8859-1')
        
        if df.empty:
            raise ValueError("CSV file is empty")
            
        logging.info(f"Extracted {len(df)} records from {CSV_PATH}")
        df.to_csv(os.path.join(DAG_FOLDER, 'files/extracted_data.csv'), index=False)
        
    except Exception as e:
        logging.error(f"Extract failed: {str(e)}")
        raise

def transform():
    """Transform data from extract task"""
    try:
        df = pd.read_csv(os.path.join(DAG_FOLDER, 'files/extracted_data.csv'))
        
        df.rename(columns={
            'ORDERNUMBER': 'OrderID',
            'ORDERDATE': 'OrderDate',
            'PRODUCTCODE': 'ProductSerial',
            'PRODUCTLINE': 'Category',
            'TERRITORY': 'Region',
            'QUANTITYORDERED': 'QuantityInStock',
            'PRICEEACH': 'Price',
            'SALES': 'Sales'
        }, inplace=True)

        numeric = ['QuantityInStock', 'Price', 'Sales']
        Category = ['ProductSerial', 'Category', 'Region']
        
        df.drop_duplicates(inplace=True)

        df['OrderDate'] = pd.to_datetime(df['OrderDate'], errors='coerce')
        df = df[df['OrderDate'].notna()]

        num_imputer = SimpleImputer(strategy='mean')
        df[numeric] = num_imputer.fit_transform(df[numeric])

        cat_imputer = SimpleImputer(strategy='most_frequent')
        df[Category] = cat_imputer.fit_transform(df[Category])
              
        logging.info("Data transformation complete")
        df.to_csv(TRANSFORMED_DATA_PATH, index=False)
        
    except Exception as e:
        logging.error(f"Transform failed: {str(e)}")
        raise

def load():
    """Load transformed data into MySQL"""
    try:
        df = pd.read_csv(TRANSFORMED_DATA_PATH)
        
        connection = pymysql.connect(
            host='host.docker.internal',
            user='root',
            password='Ibrahim2001@',
            database='mydatabase'
        )
        
        with connection.cursor() as cursor:
            for _, row in df.iterrows():
                sql = """
                    INSERT INTO sales_data (
                        OrderID, ProductSerial, Category, OrderDate, Region,
                        QuantityInStock, Price, Sales
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        ProductSerial=VALUES(ProductSerial),
                        Category=VALUES(Category),
                        OrderDate=VALUES(OrderDate),
                        Region=VALUES(Region),
                        QuantityInStock=VALUES(QuantityInStock),
                        Price=VALUES(Price),
                        Sales=VALUES(Sales);
                """
                cursor.execute(sql, (
                    int(row['OrderID']),
                    row['ProductSerial'],
                    row['Category'],
                    row['OrderDate'],
                    row['Region'],
                    float(row['QuantityInStock']),
                    float(row['Price']),
                    float(row['Sales'])
                ))
            
            connection.commit()
            logging.info(f"Successfully loaded/updated {len(df)} rows")
            
    except Exception as e:
        logging.error(f"Load failed: {str(e)}")
        raise
    finally:
        connection.close()

with DAG(
    'sales_etl_pipeline',
    default_args=default_args,
    schedule_interval='@daily',
    catchup=False,
    tags=['etl', 'production'],
    doc_md="""### Sales ETL Pipeline
    Uses CSV from DAG folder and pymysql with ON DUPLICATE KEY UPDATE
    """
) as dag:

    create_table_task = PythonOperator(
        task_id='create_table',
        python_callable=create_table,
        do_xcom_push=False
    )

    extract_task = PythonOperator(
        task_id='extract',
        python_callable=extract,
        do_xcom_push=False
    )

    transform_task = PythonOperator(
        task_id='transform',
        python_callable=transform,
        do_xcom_push=False
    )

    load_task = PythonOperator(
        task_id='load',
        python_callable=load,
        do_xcom_push=False
    )

    create_table_task >> extract_task >> transform_task >> load_task