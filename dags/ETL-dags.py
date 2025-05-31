from airflow import DAG
from airflow.operators.python_operator import PythonOperator
from datetime import datetime
import pandas as pd
from sklearn.preprocessing import MinMaxScaler, LabelEncoder
import pymysql
import os 

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2025, 5, 29),
    'retries': 1,
}

csv_path = '/opt/airflow/data/sales_data_sample.csv'
transformed_df = None

def extract():
    df = pd.read_csv(csv_path, encoding='ISO-8859-1')
    
    # ✅ Ensure the target directory exists
    os.makedirs('/opt/airflow/dags/files', exist_ok=True)
    
    df.to_csv('/opt/airflow/dags/files/extracted_data.csv', index=False)

def transform():
    global transformed_df
    df = pd.read_csv('/opt/airflow/dags/files/extracted_data.csv')
    df.rename(columns={
        'ORDERNUMBER': 'order_id',
        'ORDERDATE': 'order_date',
        'PRODUCTCODE': 'product',
        'PRODUCTLINE': 'category',
        'TERRITORY': 'region',
        'QUANTITYORDERED': 'quantity',
        'PRICEEACH': 'unit_price',
        'SALES': 'total_price'
    }, inplace=True)

    df.drop_duplicates(inplace=True)

    categorical_cols = ['product', 'category', 'region']
    numerical_cols = ['quantity', 'unit_price', 'total_price']

    for col in categorical_cols:
        if col in df.columns:
            df[col].fillna(df[col].mode()[0], inplace=True)

    for col in numerical_cols:
        if col in df.columns:
            df[col].fillna(df[col].mean(), inplace=True)

    df['order_date'] = pd.to_datetime(df['order_date'], errors='coerce')
    df.dropna(subset=['order_id', 'order_date'], inplace=True)
    df['order_year'] = df['order_date'].dt.year

    le_dict = {}
    for col in categorical_cols:
        le = LabelEncoder()
        df[col] = le.fit_transform(df[col].astype(str))
        le_dict[col] = le

    scaler = MinMaxScaler()
    df[numerical_cols] = scaler.fit_transform(df[numerical_cols])

    transformed_df = df
    df.to_csv('/opt/airflow/dags/files/transformed_data.csv', index=False)  # Optional: save result
    return None  # Prevent Airflow from trying to serialize DataFrame

def load():
    global transformed_df
    if transformed_df is None:
        transformed_df = pd.read_csv('/opt/airflow/dags/files/transformed_data.csv')

    connection = pymysql.connect(
        host='host.docker.internal',
        user='root',
        password='root',
        database='sales3'
    )
    cursor = connection.cursor()

    for _, row in transformed_df.iterrows():
        sql = """
            INSERT INTO sales_data (
                order_id, product, category, order_date, region,
                quantity, unit_price, total_price, order_year
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                product=VALUES(product),
                category=VALUES(category),
                order_date=VALUES(order_date),
                region=VALUES(region),
                quantity=VALUES(quantity),
                unit_price=VALUES(unit_price),
                total_price=VALUES(total_price),
                order_year=VALUES(order_year);
        """
        cursor.execute(sql, (
            int(row['order_id']),
            int(row['product']),
            int(row['category']),
            row['order_date'],
            int(row['region']),
            float(row['quantity']),
            float(row['unit_price']),
            float(row['total_price']),
            int(row['order_year'])
        ))

    connection.commit()
    cursor.close()
    connection.close()

with DAG('sales_etl_label_encode',
         default_args=default_args,
         schedule_interval='@daily',
         catchup=False) as dag:

    extract_task = PythonOperator(
        task_id='extract',
        python_callable=extract,
        do_xcom_push=False  # ✅ Prevent XCom error
    )

    transform_task = PythonOperator(
        task_id='transform',
        python_callable=transform,
        do_xcom_push=False  # ✅ Prevent XCom error
    )

    load_task = PythonOperator(
        task_id='load',
        python_callable=load,
        do_xcom_push=False  # ✅ Prevent any large object issues
    )

    extract_task >> transform_task >> load_task
