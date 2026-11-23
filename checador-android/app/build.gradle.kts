import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// Llave de firma de PRODUCCION. No vive en el repo: se pasa la ruta de su archivo
// de propiedades con -PchecadorKeystoreProps=... Una tableta solo acepta
// actualizaciones firmadas con la MISMA llave: perderla obliga a reinstalar.
val llaveProps: String? = (project.findProperty("checadorKeystoreProps") as String?)
    ?: System.getenv("CHECADOR_KEYSTORE_PROPS")

android {
    namespace = "net.ferba.checador"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "net.ferba.checador"
        minSdk = 26
        targetSdk = 35
        versionCode = (project.findProperty("checadorVersionCode") as String?)?.toInt() ?: 3
        versionName = (project.findProperty("checadorVersionName") as String?) ?: "1.1.1"
    }

    signingConfigs {
        if (llaveProps != null && file(llaveProps).exists()) {
            create("release") {
                val p = Properties().apply { file(llaveProps).inputStream().use { load(it) } }
                storeFile = file(p.getProperty("storeFile"))
                storePassword = p.getProperty("storePassword")
                keyAlias = p.getProperty("keyAlias")
                keyPassword = p.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.activity)
}
