参考了keysight的说明文档和ITU M.1371-5建议书，实现了AIS的GMSK信号发射，可以下载到keysight的信号发生器中
真实码文来自AIS接收机收到的数据
频谱仪验证通过，用信号发生器连接AIS接收机，可以正确译码输出。用RM4的AIS接收板，灵敏度可以测试到-114dBm的水平
1. ais_gmsk_n5182a.py is main program
2. advdm_bits.py: encoding the real package into bit
3. probe_scpi.py: connect to the keysight signal generator through scpi
