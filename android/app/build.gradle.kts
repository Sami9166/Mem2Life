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
        minSdk = 31
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Meta Wearables Device Access Toolkit 설정.
        // Developer Mode에서는 0/0으로 둔다 (실기기 배포 시 Wearables Developer
        // Center에서 발급받은 값으로 교체). 하드코딩하지 않고 gradle.properties나
        // 환경변수로 주입할 수 있도록 프로젝트 속성을 우선 사용한다.
        manifestPlaceholders["mwdat_application_id"] =
            (project.findProperty("MWDAT_APPLICATION_ID") as String?) ?: "0"
        manifestPlaceholders["mwdat_client_token"] =
            (project.findProperty("MWDAT_CLIENT_TOKEN") as String?) ?: "0"
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

    // Meta Wearables DAT SDK. mwdat-display는 이 프로젝트 범위(카메라+오디오
    // 스트리밍 수신)에 필요 없어 제외했다 — 디스플레이 글래스 대응이 필요해지면
    // 추가한다. mwdat-mockdevice는 공식 CameraAccess 샘플과 동일하게 모든
    // 빌드 타입에 포함한다(1단계는 실기기가 없어 Mock Device Kit이 항상 필요).
    implementation(libs.mwdat.core)
    implementation(libs.mwdat.camera)
    implementation(libs.mwdat.mockdevice)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
}
