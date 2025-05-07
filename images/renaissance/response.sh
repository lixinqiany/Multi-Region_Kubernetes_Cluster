#!/usr/bin/expect -f

# 生成唯一时间戳，用于结果文件名和描述
set ts [clock format [clock seconds] -format {%Y%m%d%H%M%S}]

# 启动 Renaissance 测试
spawn phoronix-test-suite run renaissance
set timeout 30

# 交互：安装提示、选择第 8 项 Scala Dotty、保存结果、命名
expect {
    "Would you like to stop and install these tests now (Y/n):" {
        send "Y\r"; exp_continue
    }
    "** Multiple items can be selected" {
        send "8\r"; exp_continue
    }
    "Would you like to save these test results (Y/n):" {
        send "y\r"; exp_continue
    }
    "Enter a name for the result file:" {
        send "scala-dotty-${ts}\r"; exp_continue
    }
    "Enter a unique name to describe this test run / configuration:" {
        send "ScalaDotty_${ts}\r"; exp_continue
    }
    "New Description:" {
        send "\r"; exp_continue
    }
}

# 等待测试执行完成并捕获平均值输出
expect {
    -re "Test 1 of 1" {
        # 取消超时，等待实际运行结束
        set timeout -1
        expect {
            -re "Average:.*" {
                # 收到平均值后，恢复默认超时处理后续交互
                set timeout 30
                expect {
                    "Do you want to view the text results of the testing (Y/n):" {
                        send "n\r"; exp_continue
                    }
                    "Would you like to upload the results to OpenBenchmarking.org (y/n):" {
                        send "n\r"; exp_continue
                    }
                    eof {
                        exit 0
                    }
                }
            }
            timeout {
                # 如果实际运行超时，可判为失败
                exit 1
            }
        }
    }
}

exit 0
