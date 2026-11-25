package com.olddriverdoge.doubaocli;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

/**
 * 用 Termux 官方的 RUN_COMMAND 接口，从 App 里拉一次 `doubao-gui start`。
 *
 * 前置条件（用户侧，一次性）：
 *   1. Termux 里跑过 install.sh（有 doubao-gui 命令）
 *   2. Termux 设置里打开 allow-external-apps
 *
 * 注意：RUN_COMMAND 起的命令没有 TTY，所以这里只能做“启动后台服务”这种
 * 不需要交互的事；真正的对话仍然由 Termux 里的服务执行。
 */
public class TermuxBridge {

    private static final String TERMUX_PACKAGE = "com.termux";
    private static final String TERMUX_SERVICE = "com.termux.app.RunCommandService";
    private static final String TERMUX_ACTION = "com.termux.RUN_COMMAND";
    private static final String TERMUX_BASH = "/data/data/com.termux/files/usr/bin/bash";
    private static final String TERMUX_HOME = "/data/data/com.termux/files/home";

    public static boolean installed(Context context) {
        try {
            context.getPackageManager().getPackageInfo(TERMUX_PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException error) {
            return false;
        }
    }

    public static String boot(Context context) {
        if (context == null) {
            return "no-context";
        }
        if (!installed(context)) {
            return "termux-missing";
        }
        try {
            Intent intent = new Intent(TERMUX_ACTION);
            intent.setClassName(TERMUX_PACKAGE, TERMUX_SERVICE);
            intent.putExtra("com.termux.RUN_COMMAND_PATH", TERMUX_BASH);
            intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-lc", "doubao-gui start"});
            intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", TERMUX_HOME);
            intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
            context.startService(intent);
            return "sent";
        } catch (Exception error) {
            return "error:" + error.getClass().getSimpleName();
        }
    }
}
