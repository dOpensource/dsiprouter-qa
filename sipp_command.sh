# Values that should be set as environment variables

# $1 - Scenario File
# $2 - TEST_FROM_NUMBER

sipp -sf $1 -s $2 merge.dsiprouter.net -trace_msg -l 15 -d 60000 -r 1
