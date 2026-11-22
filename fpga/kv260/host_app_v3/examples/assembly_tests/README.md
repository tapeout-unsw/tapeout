# Precompiled assembly examples

Choose **Load binary unchanged** in the web app. `mc_uart.bin` prints
`HELLOWORLDP`; the byte-lane, cache-thrash and lock-stress tests print `P`
on success or `F` followed by an error code on failure. Check the UART output:
the app's completion status only indicates both cores wrote EXIT.
