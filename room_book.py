"""
author: ningbao & guoguoly
date: 2022.3.17
"""

import requests
import datetime
import json
import time
import threading
import config
import uconfig
import sys

gap_times = 1  # 每轮搜索的间隔时间 （一轮指获取一次所有信息表，并依次尝试可能的预约选择后，多线程模式下会等本轮所有请求反馈结束后再开启下一轮）
threading_mode = 1

# 多线程，输出显示可能会覆盖，看不到所有的信息，已是否预约到为准   0则单线程预约
# 多线程下，比较可能不同时间预约的研讨间是不同的，需要确保几个时间段所预约的研讨间相同，则使用单线程预约，
# 单人研讨间 建议使用单线程，因为反馈请求的时间似乎挺快，单线程下预约反馈速度足够快了，而多人研讨间反馈请求的时间不知道为什么总会慢一些，卡住后续时间预约，所以建议使用多线程。

# countdown_mode = 0 #倒计时模式，卡零点自动预约后天的研讨间  未完成


class AutoBookRoom:
    def __init__(self, student_id, passwd, room_type, member_id_list, book_date, time_period):
        # run interval 执行的间隔时间 单位：秒
        self.interval = 1

        # basic info
        self.student_id = student_id
        self.passwd = passwd
        self.room_type = room_type

        # 默认为单人预约的情况
        self.min_usr = 1
        self.max_usr = 1
        self.room_class_id = config.room_info["single"]["class_id"]

        # member id / inter id list
        self.member_id_list = member_id_list
        self.inter_id_list = []

        # 预约日期和预约时间段
        self.book_date = book_date
        self.time_period = time_period

        # 预定状态，与time period相等
        self.book_status = [0 for x in range(0, len(self.time_period))]

        # session maintain
        self.session = None
        self.url_get_room_info = ""
        self.url_reserve = "http://10.9.4.215/ClientWeb/pro/ajax/reserve.aspx?"

        # 当前类型的房间的预约信息
        self.room_info = {}

    # 参数 学生id 和 密码
    def getSession(self):
        # IC系统登录
        header = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Connection': 'keep-alive',
            'Content-Length': '33',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Host': '10.9.4.215',
            'Origin': 'http://10.9.4.215',
            'Referer': 'http://10.9.4.215/ClientWeb/xcus/ic2/Default.aspx',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36 Edg/96.0.1054.62',
            'X-Requested-With': 'XMLHttpRequest',
        }
        login_url = "http://10.9.4.215/ClientWeb/pro/ajax/login.aspx"

        self.session = requests.session()

        data = {
            'id': self.student_id,
            'pwd': self.passwd,
            'act': 'login'
        }

        result = self.session.post(login_url, headers=header, data=data).json()

        if result["msg"] == "ok":
            print("login succeed")
            return 1
        else:
            print("login fail")
            return 0

    # 检查user config是否合法
    def infoCheckAndInit(self):
        # todo check完备性
        if self.room_type != "" and self.room_type in config.room_info.keys():
            self.min_usr = config.room_info[self.room_type]["min_usr"]
            self.max_usr = config.room_info[self.room_type]["max_usr"]
            self.room_class_id = config.room_info[self.room_type]["class_id"]
        else:
            print("请填写要预约房间类型")
            return 0

        # 根据room type check member数量
        if len(uconfig.mid_list) > self.max_usr or len(uconfig.mid_list) < self.min_usr:
            print("预约人数量与房间要求人员数量不符")
            return 0
        else:
            # 将学生id映射为inter id
            self.getInterIdList()

            print(self.inter_id_list)

        # 初始化日期
        # 如果未指定日期，则默认是第二天
        if not self.book_date:
            self.book_date = (datetime.datetime.today() + datetime.timedelta(days=1)).strftime("%Y%m%d")

        # 初始化获取room info 的url
        self.url_get_room_info = f"http://10.9.4.215/ClientWeb/pro/ajax/device.aspx?classkind=1&display=cld&md=d&class_id={self.room_class_id}&cld_name=default&date={self.book_date}&act=get_rsv_sta&"

        return 1

    # student id -> inter id 获取内部id
    def getInterIdList(self):
        print("------member info------")
        for m_id in self.member_id_list:
            print(m_id)
            info = self.session.get(
                f'http://10.9.4.215/ClientWeb/pro/ajax/data/searchAccount.aspx?type=&ReservaApply=ReservaApply&term={m_id}').json()
            self.inter_id_list.append(info[0]['id'])
            print(info[0]["name"])

    # 获取当前类型的房间的信息
    def getRoomInfo(self):
        # 多线程
        # global thread_list
        result = requests.get(self.url_get_room_info).json()['data']

        # room filter 筛选房间
        room_info = []
        for i in result:
            # 过滤掉关闭的房间
            if i["state"] == "close":
                continue

            # 根据用户喜好过滤房间
            if i["name"] in uconfig.room_list:
                room_info.append(i)

        self.room_info = room_info

    # 单线程模式预定
    def makeOrder(self):
        # todo schedule optimize
        # 当前策略 遍历目标时间段，遍历房间，遍历房间已预约时间段
        # 遍历预约的时间段
        for i in range(len(self.time_period)):
            # 判断当前时间段是否已经完成
            if self.book_status[i]:
                continue

            period = self.time_period[i]

            # 预约的时间段的开始和结束时间
            p_start = int(period[0])
            p_end = int(period[1])
            for r_info in self.room_info:  # 研讨间
                can_book = 1
                # 获取研讨间的信息
                dev_name = r_info["devName"]
                dev_id = r_info["devId"]
                lab_id = r_info["labId"]
                kind_id = r_info["kindId"]
                # print(dev_name, dev_id, lab_id, kind_id)

                # 遍历当前已有的预约 current order
                for co in r_info['ts']:  # 当前研讨间已有的预约
                    # 返回的time格式 "start": "2022-03-27 09:40"
                    c_start = int(co['start'][-5:-3] + co['start'][-2:])
                    c_end = int(co['end'][-5:-3] + co['end'][-2:])

                    # 当前时间段无重合，遍历下个时间段
                    if c_end < p_start or c_start > p_end:
                        continue
                    else:
                        can_book = 0
                        break

                if can_book:
                    print(f'开始预约{dev_name},时间{p_start} ~ {p_end}')
                    result = self.order(dev_id, lab_id, kind_id, period)

                    if result:
                        # 当前period预约成功
                        print("预约成功")
                        # 从time period中删除该时段
                        self.book_status[i] = 1
                        break
                    else:
                        print("预约失败")

    # 单次预约操作函数
    def order(self, dev_id, lab_id, kind_id, period):
        ts = datetime.datetime.strptime(self.book_date, "%Y%m%d").strftime("%Y-%m-%d") + ' ' + period[0][:2] + ':' + period[
                                                                                                                         0][
                                                                                                                     2:]
        te = datetime.datetime.strptime(self.book_date, "%Y%m%d").strftime("%Y-%m-%d") + ' ' + period[1][:2] + ':' + period[
                                                                                                                         1][
                                                                                                                     2:]

        # 对 m_i_list string fy
        mb_list_str = "$"
        for i in self.inter_id_list:
            mb_list_str += (str(i) + ",")
        mb_list_str = mb_list_str[:-1]

        data = {
            'dev_id': dev_id,
            'lab_id': lab_id,
            'kind_id': kind_id,
            'type': 'dev',
            'start': ts,
            'end': te,
            'start_time': period[0],
            'end_time': period[1],
            'act': 'set_resv',
            'mb_list': mb_list_str,
            'min_user': self.min_usr,
            'max_user': self.max_usr
        }

        result = self.session.get(self.url_reserve, data=data).json()['msg']
        print("result:", result)
        if "操作成功" in result:
            return 1
        else:
            return 0

        # if "操作成功" in r2:
        #     print(f"{devName}{start1}到{end1}预约成功")
        #     try:
        #         time_list.remove(time1)
        #     except:
        #         pass
        #     finally:
        #         pass
        # if "已有预约" in r2:
        #     print("同时段已有预约")
        #     try:
        #         time_list.remove(time1)
        #     except:
        #         pass
        #     finally:
        #         pass
        # return r2

    # 自动运行
    def run(self):
        # todo 到了0点自动开抢，激活时间段 00.00 ~ 00.15

        count = 0
        while sum(self.book_status) != len(self.book_status) and count < 900:
            # 更新房间的信息，预定
            self.getRoomInfo()
            self.makeOrder()
            time.sleep(self.interval)
            count += 1

        for i in range(len(self.time_period)):
            period = self.time_period[i]
            if self.book_status[i] == 1:
                status = "成功"
            else:
                status = "失败"

            print(f"时间段：{period[0]} ~ {period[1]} 预约状态：{status}")


if __name__ == "__main__":

    auto_book = AutoBookRoom(uconfig.student_id, uconfig.passwd, uconfig.room_type, uconfig.mid_list, uconfig.date,
                             uconfig.time_period)

    auto_book.getSession()

    # check user config的合法性
    auto_book.infoCheckAndInit()

    # auto_book.getRoomInfo()
    # auto_book.run()

    # 倒计时模式
    while True:
        now = datetime.datetime.now()
        hour = now.hour
        minute = now.minute
        second = now.second
        print(f"---wait---{hour}:{minute}:{second}")

        if hour == 23 and minute == 59 and second > 45:
            print("start auto book")
            auto_book.getRoomInfo()
            auto_book.run()
            break

        # 睡眠1s
        time.sleep(1)

    print("预设时间列表已经预约完成")
