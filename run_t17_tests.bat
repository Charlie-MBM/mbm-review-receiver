@echo off
cd /d C:\Users\charl\GitHub\mbm-review-receiver
echo === _nurture_t5d_test.py === > t17_test_output.txt 2>&1
py _nurture_t5d_test.py >> t17_test_output.txt 2>&1
echo. >> t17_test_output.txt
echo === send_nurture_sequence.py --dry-run === >> t17_test_output.txt
py send_nurture_sequence.py --dry-run >> t17_test_output.txt 2>&1
echo. >> t17_test_output.txt
echo DONE >> t17_test_output.txt
