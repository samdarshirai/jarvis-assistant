package com.jarvis.jarvis_app

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

class JarvisWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        // Own action and request code: otherwise filterEquals matches BootReceiver's intent (extras are ignored),
        // and Android would hand the boot notification this EXTRA_START PendingIntent.
        val tap = PendingIntent.getActivity(
            context, 1,
            Intent(context, MainActivity::class.java)
                .setAction("com.jarvis.jarvis_app.WIDGET_START")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(MainActivity.EXTRA_START, true),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val views = RemoteViews(context.packageName, R.layout.jarvis_widget)
            .apply { setOnClickPendingIntent(R.id.widget_root, tap) }
        ids.forEach { manager.updateAppWidget(it, views) }
    }
}
