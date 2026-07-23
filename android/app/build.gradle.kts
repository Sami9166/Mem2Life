import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.jetbrains.kotlin.android)
    alias(libs.plugins.compose.compiler)
}

android {
    namespace = "com.mem2life.companion"
    compileSdk = 36

    buildFeatures {
        buildConfig = true
        compose = true
    }

    defaultConfig {
        applicationId = "com.mem2life.companion"
        // Vuzix Blade 2는 Android 11(API 30)이다 — minSdk 30 이하를 유지해야
        // 실기기에 설치된다. API 31+ 전용 API 사용 시 반드시 버전 가드를 둘 것.
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }

    testOptions {
        unitTests {
            isReturnDefaultValues = true
        }
    }
}

kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_17 } }

dependencies {
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.material.icons.extended)
    implementation(libs.androidx.material3)
    implementation(libs.kotlinx.collections.immutable)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)

    // Vuzix Blade 2는 표준 Android API(Camera2, AudioRecord)만으로 개발한다 —
    // 별도 벤더 SDK 의존성이 없다. (음성 명령이 필요해지면 Vuzix Speech SDK를,
    // 터치패드 메뉴 UI가 필요해지면 com.vuzix:hud-actionmenu를 추가 검토한다.)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
}
