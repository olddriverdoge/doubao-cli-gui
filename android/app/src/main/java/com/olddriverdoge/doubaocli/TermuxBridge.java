package com.olddriverdoge.doubaocli;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;

import java.util.List;

/**
 * 用 Termux 官方的 RUN_COMMAND 接口，从 App 里拉一次 `doubao-gui start`。
 *
 * 不硬编码包名：ZeroTermux 等衍生版包名可能不是 com.termux，这里按 action
 * 查询哪个 App 声明了 com.termux.RUN_COMMAND，找到谁就用谁。
 *
 * 前置条件（用户侧，一次性）：
 *   1. Termux 里跑过 install.sh（有 doubao-gui 命令）
 *   2. ~/.termux/termux.properties 里 allow-external-apps=true（设置界面没有这一项）
 *   3. 系统设置里给本 App 授予“在 Termux 中执行命令”权限（RUN_COMMAND）
 */
public class TermuxBridge {

    private static final String ACTION = "com.termux.RUN_COMMAND";
    private static final String PERMISSION = "com.termux.permission.RUN_COMMAND";
    private static final String[] FALLBACK_PACKAGES = {
        "com.termux", "com.zerotermux", "com.termux.zerotermux", "io.neoterm"
    };

    private static ComponentName findService(Context context) {
        try {
            List<ResolveInfo> services = context.getPackageManager()
                .queryIntentServices(new Intent(ACTION), PackageManager.MATCH_ALL);
            if (services == null || services.isEmpty()) {
                return null;
            }
            ResolveInfo info = services.get(0);
            if (info.serviceInfo == null) {
                return null;
            }
            return new ComponentName(info.serviceInfo.packageName, info.serviceInfo.name);
        } catch (Exception error) {
            return null;
        }
    }

    private static String resolvePackage(Context context) {
        ComponentName service = findService(context);
        if (service != null) {
            return service.getPackageName();
        }
        for (String candidate : FALLBACK_PACKAGES) {
            try {
                context.getPackageManager().getPackageInfo(candidate, 0);
                return candidate;
            } catch (PackageManager.NameNotFoundException ignored) {
            }
        }
        return null;
    }

    public static boolean installed(Context context) {
        return resolvePackage(context) != null;
    }

    public static boolean hasPermission(Context context) {
        return context.checkSelfPermission(PERMISSION) == PackageManager.PERMISSION_GRANTED;
    }

    /** 给前端/日志看的真实状态。 */
    public static String describe(Context context) {
        String pkg = resolvePackage(context);
        if (pkg == null) {
            return "no-termux";
        }
        if (!hasPermission(context)) {
            return "need-permission:" + pkg;
        }
        return "ready:" + pkg;
    }

    public static String boot(Context context) {
        if (context == null) {
            return "no-context";
        }
        ComponentName service = findService(context);
        if (service == null) {
            return "termux-missing";
        }
        if (!hasPermission(context)) {
            return "permission-denied";
        }
        try {
            ApplicationInfo info = context.getPackageManager()
                .getApplicationInfo(service.getPackageName(), 0);
            String base = info.dataDir;
            Intent intent = new Intent(ACTION);
            intent.setComponent(service);
            intent.putExtra("com.termux.RUN_COMMAND_PATH", base + "/files/usr/bin/bash");
            intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-lc", "doubao-gui start"});
            intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", base + "/files/home");
            intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
            context.startService(intent);
            return "sent";
        } catch (Exception error) {
            return "error:" + error.getClass().getSimpleName();
        }
    }

    /** 打开 Termux 主界面，方便用户手动敲命令。 */
    public static String openApp(Context context) {
        String pkg = resolvePackage(context);
        if (pkg == null) {
            return "termux-missing";
        }
        try {
            Intent launch = context.getPackageManager().getLaunchIntentForPackage(pkg);
            if (launch == null) {
                return "no-launcher";
            }
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(launch);
            return "opened:" + pkg;
        } catch (Exception error) {
            return "error:" + error.getClass().getSimpleName();
        }
    }
}
