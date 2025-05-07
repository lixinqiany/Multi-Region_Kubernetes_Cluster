#!/usr/bin/expect -f

# 生成唯一时间戳，用于结果文件名和描述
set ts [clock format [clock seconds] -format {%Y%m%d%H%M%S}]

# 启动 MBW 测试
spawn phoronix-test-suite run mbw
set timeout 30

# 预交互：选择测试、数组大小、保存选项、命名
expect {
    "Test:" {
        send "3\r"; exp_continue
    }
    "Array Size:" {
        send "3\r"; exp_continue
    }
    "Would you like to save these test results (Y/n):" {
        send "Y\r"; exp_continue
    }
    "Enter a name for the result file:" {
        send "mbw-copy-autotest-${ts}\r"; exp_continue
    }
    "Enter a unique name to describe this test run / configuration:" {
        send "mbw-AutoTest-${ts}\r"; exp_continue
    }
    "New Description:" {
        send "\r"; exp_continue
    }
}

# 等待两个子测试依次完成
for {set i 1} {$i <= 2} {incr i} {
    expect {
        -re "Test $i of 2" {
            set timeout -1
            expect {
                -re "Average:.*MiB/s" {
                    # 捕获到平均值，继续
                }
                timeout {
                    puts "\n[ERROR] Test $i 超时"; exit 1
                }
                eof {
                    puts "\n[ERROR] Test $i 未料想 EOF"; exit 1
                }
                -re "ERROR|FAILED" {
                    puts "\n[ERROR] Test $i 失败"; exit 1
                }
                -re "Started Run \\d+ @ \\d+:\\d+:\\d+" {
                    exp_continue
                }
            }
        }
    }
}

# 测试后交互：不查看文本、不上传
set timeout 30
expect {
    "Do you want to view the text results of the testing (Y/n):" {
        send "n\r"; exp_continue
    }
    "Would you like to upload the results to OpenBenchmarking.org (y/n):" {
        send "n\r"; exp_continue
    }
    eof {
        # 正常退出
        exit 0
    }
}

exit 0
