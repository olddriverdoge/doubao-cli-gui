package com.olddriverdoge.doubaocli;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 壳很薄：起 Python 网关 → 等端口就绪 → 把 WebView 指过去。
 * 真正的逻辑（静态页、反向代理、Termux 探活）都在 gateway.py。
 */
public class MainActivity extends Activity {

    private static final int PORT = 8790;
    private static final int PROBE_INTERVAL_MS = 100;
    private static final int PROBE_ATTEMPTS = 200;

    private WebView web;
    private final AtomicBoolean booting = new AtomicBoolean(false);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0B0D11"));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        web.setWebViewClient(new WebViewClient());
        setContentView(web);

        startGateway();
    }

    private void startGateway() {
        if (!booting.compareAndSet(false, true)) {
            return;
        }
        Thread pythonThread = new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                Python.getInstance().getModule("gateway").callAttr("serve", PORT);
            } catch (Throwable error) {
                error.printStackTrace();
            }
        }, "doubao-gateway");
        pythonThread.setDaemon(true);
        pythonThread.start();
        waitForPort();
    }

    private void waitForPort() {
        Thread probe = new Thread(() -> {
            for (int attempt = 0; attempt < PROBE_ATTEMPTS; attempt++) {
                if (portReady()) {
                    final String url = "http://127.0.0.1:" + PORT + "/";
                    runOnUiThread(() -> web.loadUrl(url));
                    return;
                }
                try {
                    Thread.sleep(PROBE_INTERVAL_MS);
                } catch (InterruptedException ignored) {
                    return;
                }
            }
            runOnUiThread(this::showStartupFailure);
        }, "doubao-probe");
        probe.setDaemon(true);
        probe.start();
    }

    private boolean portReady() {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", PORT), 300);
            return true;
        } catch (IOException error) {
            return false;
        }
    }

    private void showStartupFailure() {
        String html = "<html><body style='background:#0b0d11;color:#e7eaf0;font-family:sans-serif;padding:24px'>"
                + "<h3>网关没起来</h3>"
                + "<p style='color:#8b94a3;font-size:14px'>Python 侧初始化失败。重开一次；若仍失败，用 adb logcat 看 doubao-gateway 线程的堆栈。</p>"
                + "</body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    @Override
    public void onBackPressed() {
        if (web == null) {
            super.onBackPressed();
            return;
        }
        web.evaluateJavascript("(typeof window.__sheetOpen === 'function') && window.__sheetOpen()", value -> {
            if ("true".equals(value)) {
                web.evaluateJavascript("window.__closeSheet && window.__closeSheet()", null);
            } else {
                finish();
            }
        });
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}
