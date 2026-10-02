package com.jarvis.jarvis_app

import android.content.Intent
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

class JarvisRecognitionService : RecognitionService() {
    override fun onStartListening(intent: Intent?, callback: Callback?) { callback?.error(SpeechRecognizer.ERROR_CLIENT) }
    override fun onCancel(callback: Callback?) {}
    override fun onStopListening(callback: Callback?) {}
}
