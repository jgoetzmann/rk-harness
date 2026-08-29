    LDR   r0, [r4, #0]     @ 2
    LDR   r1, [r5, #0]     @ 2
    MULS  r0, r0, r1       @ 1   (32-cycle variant: 32)
    ASRS  r0, r0, #15      @ 1
    ADDS  r2, r2, r0       @ 1
    LSLS  r3, r3, #1       @ 1
    SUBS  r2, r2, r3       @ 1
    MULS  r3, r3, r1       @ 1   (32-cycle variant: 32)
    ASRS  r3, r3, #15      @ 1
    STR   r2, [r6, #0]     @ 2
