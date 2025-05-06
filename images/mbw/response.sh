#!/usr/bin/expect -f

# 启动 Phoronix Test Suite 测试
spawn phoronix-test-suite run mbw

# 设置全局超时（用于初始交互）
set timeout 30

# 处理预测试交互
expect {
    "Test:" {
        send "3\r"
        exp_continue
    }
    "Array Size:" {
        send "3\r"
        exp_continue
    }
    "Would you like to save these test results (Y/n):" {
        send "Y\r"
        exp_continue
    }
    "Enter a name for the result file:" {
        send "mbw-copy-autotest\r"
        exp_continue
    }
    "Enter a unique name to describe this test run / configuration:" {
        send "mbw-AutoTest\r"
        exp_continue
    }
    "New Description:" {
        send "\r"
        exp_continue
    }
}

# 等待两个测试依次运行
for {set i 1} {$i <= 2} {incr i} {
    expect {
        -re "Test $i of 2" {
            puts "Starting Test $i..."
            set timeout -1
            expect {
                -re "Average:.*MiB/s" {
                    puts "Test $i Success\n"
                }
                timeout {
                    puts "\nTest $i Timeout"
                    exit 1
                }
                eof {
                    puts "\nTest $i Unexpected EOF"
                    exit 1
                }
                -re "ERROR|FAILED" {
                    puts "\nTest $i Fail"
                    exit 1
                }
                # 保持活跃直到捕获到Average
                -re "Started Run \\d+ @ \\d+:\\d+:\\d+" {
                    exp_continue
                }
            }
        }
    }
}

# 处理测试后交互
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
        puts "\nDone"
    }
}

exit 0
