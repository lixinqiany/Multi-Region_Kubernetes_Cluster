#!/usr/bin/expect -f

# 设置总超时时间（12分钟 = 720000 毫秒）
after 720000 {
    puts "\n 到达12分钟，主动正常退出"
    exit 0
}

# 启动 Phoronix Test Suite 测试
spawn phoronix-test-suite run renaissance

# 设置初始交互阶段的超时
set timeout 30

# 阶段 1: 交互处理测试项选择与配置
expect {
    "Would you like to stop and install these tests now (Y/n):" {
        send "Y\r"
        exp_continue
    }
    "** Multiple items can be selected" {
        send "8\r"
        exp_continue
    }
    "Would you like to save these test results (Y/n):" {
        send "y\r"
        exp_continue
    }
    "Enter a name for the result file:" {
        send "scala-dotty-autotest\r"
        exp_continue
    }
    "Enter a unique name to describe this test run / configuration:" {
        send "Scala-Dotty-Test\r"
        exp_continue
    }
    "New Description:" {
        send "\r"
        exp_continue
    }

    # 进入测试运行阶段
    -re "Test 1 of 1" {
        puts "\n 开始测试运行..."
        set timeout -1

        expect {
            # 跳过多个运行阶段的输出
            -re "Started Run \\d+ @ \\d+:\\d+:\\d+" {
                exp_continue
            }
            -re "Average:.*" {
                puts "\n 捕获到平均值输出（测试完成）"
                set timeout 30

                expect {
                    "Do you want to view the text results of the testing (Y/n):" {
                        send "n\r"
                        exp_continue
                    }
                    "Would you like to upload the results to OpenBenchmarking.org (y/n):" {
                        send "n\r"
                        exp_continue
                    }
                    eof {
                        puts "\n 测试流程完成，正常退出"
                        exit 0
                    }
                }
            }
            -re "ERROR|FAILED" {
                puts "\n 测试失败"
                exit 1
            }
            timeout {
                puts "\n 执行过程中卡死，超时退出"
                exit 1
            }
        }
    }
}

# 默认退出路径（正常退出）
exit 0
