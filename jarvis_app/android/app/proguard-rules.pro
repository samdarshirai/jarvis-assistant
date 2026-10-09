# onnxruntime (wake word) looks its classes up by name from native code; R8 would strip them
-keep class ai.onnxruntime.** { *; }
