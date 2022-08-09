from unittest import result
import pymysql
from dbutils.pooled_db import PooledDB
from .BaseExecutor import *
import queue
import time

class MysqlExecutor(Executor):
    def __init__(self,ip,port,user,password,database):
        self.ip=ip
        self.port=int(port)
        self.user=user
        self.password=password
        self.database=database
        self.pool=None
        self.total_latency=0
        self.success_query=0
        self.lock=threading.Lock()

    def change_knob(self, knob_name, knob_value):
        if len(knob_name)!=len(knob_value):
            raise Exception("len(knob_name) should be equal to len(knob_value)")
        conn=pymysql.connect(host=self.ip,user=self.user,password=self.password,database=self.database,port=self.port)
        cur=conn.cursor()
        for i in range(len(knob_name)):
            sql="set global "+str(knob_name[i])+"="+str(knob_value[i])+";"
            cur.execute(sql)
        cur.close()
        conn.close()
    
    def reset_knob(self, knob_name: list):
        conn=pymysql.connect(host=self.ip,user=self.user,password=self.password,database=self.database,port=self.port)
        cur=conn.cursor()
        for knob in knob_name:
            sql='set @@SESSION.'+str(knob)+'=DEFAULT;'
            cur.execute(sql)
            sql='set @@GLOBAL.'+knob+'=DEFAULT;'
            cur.execute(sql)
        cur.close()
        conn.close()

    def run_job(self, thread_num, workload: list):
        self.pool=PooledDB(
            creator=pymysql,  # 使用链接数据库的模块
            maxconnections=thread_num,  # 连接池允许的最大连接数，0和None表示不限制连接数
            mincached=0,  # 初始化时，链接池中至少创建的空闲的链接，0表示不创建
            maxcached=0,  # 链接池中最多闲置的链接，0和None不限制
            maxshared=0,
            blocking=True,  # 连接池中如果没有可用连接后，是否阻塞等待。True，等待；False，不等待然后报错
            maxusage=None,  # 一个链接最多被重复使用的次数，None表示无限制
            setsession=[],  # 开始会话前执行的命令列表。
            ping=0, # ping MySQL服务端，检查是否服务可用。
            host=self.ip,
            port=int(self.port),
            user=self.user,
            password=self.password,
            database=self.database,
            charset='utf8'
        )
        self.success_query=0
        self.total_latency=0
        main_queue = queue.Queue(maxsize=0)
        p = Producer("Producer Query", main_queue, workload)
        p.setDaemon(True)
        p.start()
        t_consumer = []
        for i in range(thread_num):
            c = Consumer(i, main_queue,self.consumer_process)
            c.setDaemon(True)
            c.start()
            t_consumer.append(c)
        p.join()
        start = time.time()
        main_queue.join()
        self.pool.close()
        run_time = round(time.time() - start, 1)
        avg_lat = self.total_latency / self.success_query
        avg_qps = self.success_query / (run_time+1e-5)
        print("Latency: "+str(round(avg_lat,4))+"\nThroughput: "+str(round(avg_qps,4)))
        return avg_lat,avg_qps
 
    def execute_query_with_pool(self,sql):
        conn=None
        try:
            conn = self.pool.connection()
            cursor = conn.cursor()
            cursor.execute(sql)
            cursor.close()
            conn.commit()
            return True
        except Exception as error:
            if conn is not None:
                conn.close()
            print("mysql execute: " + str(error))
            return False

    def consumer_process(self,task_key):
        query = task_key.split('~#~')[1]
        if query:
            start = time.time()
            result=self.execute_query_with_pool(query)
            end = time.time()
            interval = end - start

            if result:
                self.lock.acquire()
                self.success_query+=1
                self.total_latency+=interval
                self.lock.release()

    def get_db_state(self):
        state_list = []
        conn=pymysql.connect(host=self.ip,user=self.user,password=self.password,database=self.database,port=self.port)
        cur=conn.cursor()
        sql = "SELECT count FROM INFORMATION_SCHEMA.INNODB_METRICS where status='enabled'"
        cur.execute(sql)
        result=cur.fetchall()
        for s in result:
            state_list.append(s['count'])
        cur.close()
        conn.close()
        return state_list

    def get_max_thread_num(self):
        conn=pymysql.connect(host=self.ip,user=self.user,password=self.password,database=self.database,port=self.port)
        cur=conn.cursor()
        sql="show variables like 'max_connections';"
        cur.execute(sql)
        result=cur.fetchall()
        cur.close()
        conn.close()
        return int(result[0][1])