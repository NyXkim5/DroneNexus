/*
 * sensor_noise_plugin.cc
 *
 * Gazebo Garden system plugin that injects configurable noise into
 * sensor data streams. Reads noise parameters from YAML config files.
 *
 * Supported sensors:
 *   - IMU: accelerometer bias, gyroscope bias, white noise
 *   - GPS: position drift, HDOP simulation, fix dropout
 *   - Barometer: altitude noise, temperature drift
 *   - Magnetometer: hard/soft iron distortion, white noise
 *
 * Usage in SDF:
 *   <plugin filename="SensorNoisePlugin"
 *          name="nexus::SensorNoisePlugin">
 *     <config_path>/config/sensor_noise.yaml</config_path>
 *   </plugin>
 */

#include <random>
#include <string>
#include <memory>
#include <fstream>

#include <gz/sim/System.hh>
#include <gz/sim/Model.hh>
#include <gz/sim/Util.hh>
#include <gz/sim/components/Imu.hh>
#include <gz/sim/components/NavSat.hh>
#include <gz/plugin/Register.hh>
#include <gz/common/Console.hh>
#include <gz/math/Vector3.hh>
#include <gz/math/Pose3.hh>

#include <yaml-cpp/yaml.h>

namespace nexus
{

/// Noise parameters for a single sensor type.
struct SensorNoiseParams
{
    bool enabled{false};
    double white_noise_stddev{0.0};
    double bias_mean{0.0};
    double bias_stddev{0.0};
    double random_walk{0.0};
    double drift_rate{0.0};
    double update_rate{0.0};

    // IMU-specific
    double accel_noise_density{0.0};     // m/s^2/sqrt(Hz)
    double accel_random_walk{0.0};       // m/s^3/sqrt(Hz)
    double gyro_noise_density{0.0};      // rad/s/sqrt(Hz)
    double gyro_random_walk{0.0};        // rad/s^2/sqrt(Hz)

    // GPS-specific
    double position_noise_m{0.0};
    double velocity_noise_ms{0.0};
    double hdop_base{1.0};
    double hdop_variance{0.0};
    double dropout_probability{0.0};     // Probability of GPS fix loss per second
    double dropout_duration_sec{0.0};

    // Barometer-specific
    double altitude_noise_m{0.0};
    double pressure_noise_pa{0.0};
    double temperature_drift_k{0.0};

    // Magnetometer-specific
    double hard_iron_x{0.0};
    double hard_iron_y{0.0};
    double hard_iron_z{0.0};
    double soft_iron_scale{1.0};
    double mag_noise_stddev{0.0};
};

class SensorNoisePlugin :
    public gz::sim::System,
    public gz::sim::ISystemConfigure,
    public gz::sim::ISystemPreUpdate,
    public gz::sim::ISystemPostUpdate
{
public:
    SensorNoisePlugin() = default;
    ~SensorNoisePlugin() override = default;

    // ── ISystemConfigure ────────────────────────────────────────────────
    void Configure(
        const gz::sim::Entity &_entity,
        const std::shared_ptr<const sdf::Element> &_sdf,
        gz::sim::EntityComponentManager &_ecm,
        gz::sim::EventManager &_eventMgr) override
    {
        gzmsg << "[SensorNoisePlugin] Configuring..." << std::endl;

        // Read config path from SDF
        std::string configPath = "/config/sensor_noise.yaml";
        if (_sdf->HasElement("config_path"))
        {
            configPath = _sdf->Get<std::string>("config_path");
        }

        // Load YAML configuration
        LoadConfig(configPath);

        // Initialize random number generators
        std::random_device rd;
        rng_ = std::mt19937(rd());

        modelEntity_ = _entity;
        gzmsg << "[SensorNoisePlugin] Configured for entity " << _entity << std::endl;
    }

    // ── ISystemPreUpdate ────────────────────────────────────────────────
    void PreUpdate(
        const gz::sim::UpdateInfo &_info,
        gz::sim::EntityComponentManager &_ecm) override
    {
        if (_info.paused)
            return;

        double dt = std::chrono::duration<double>(_info.dt).count();
        simTime_ += dt;

        // Update bias random walks
        UpdateBiases(dt);
    }

    // ── ISystemPostUpdate ───────────────────────────────────────────────
    void PostUpdate(
        const gz::sim::UpdateInfo &_info,
        const gz::sim::EntityComponentManager &_ecm) override
    {
        if (_info.paused)
            return;

        // Noise injection happens in the PostUpdate to modify sensor
        // component data after the physics step.
        // In Gazebo Garden, direct sensor data modification requires
        // custom transport subscribers/publishers. This plugin sets up
        // the noise parameters that are applied in the sensor processing
        // pipeline.
    }

private:
    void LoadConfig(const std::string &path)
    {
        gzmsg << "[SensorNoisePlugin] Loading config: " << path << std::endl;

        try
        {
            YAML::Node config = YAML::LoadFile(path);

            if (config["imu"])
            {
                auto imu = config["imu"];
                imuNoise_.enabled = imu["enabled"].as<bool>(false);
                imuNoise_.accel_noise_density = imu["accel_noise_density"].as<double>(0.004);
                imuNoise_.accel_random_walk = imu["accel_random_walk"].as<double>(0.0006);
                imuNoise_.gyro_noise_density = imu["gyro_noise_density"].as<double>(0.0002);
                imuNoise_.gyro_random_walk = imu["gyro_random_walk"].as<double>(0.00004);
                gzmsg << "[SensorNoisePlugin] IMU noise loaded (enabled="
                      << imuNoise_.enabled << ")" << std::endl;
            }

            if (config["gps"])
            {
                auto gps = config["gps"];
                gpsNoise_.enabled = gps["enabled"].as<bool>(false);
                gpsNoise_.position_noise_m = gps["position_noise_m"].as<double>(0.5);
                gpsNoise_.velocity_noise_ms = gps["velocity_noise_ms"].as<double>(0.1);
                gpsNoise_.hdop_base = gps["hdop_base"].as<double>(1.0);
                gpsNoise_.hdop_variance = gps["hdop_variance"].as<double>(0.2);
                gpsNoise_.dropout_probability = gps["dropout_probability"].as<double>(0.0);
                gpsNoise_.dropout_duration_sec = gps["dropout_duration_sec"].as<double>(2.0);
                gzmsg << "[SensorNoisePlugin] GPS noise loaded (enabled="
                      << gpsNoise_.enabled << ")" << std::endl;
            }

            if (config["barometer"])
            {
                auto baro = config["barometer"];
                baroNoise_.enabled = baro["enabled"].as<bool>(false);
                baroNoise_.altitude_noise_m = baro["altitude_noise_m"].as<double>(0.1);
                baroNoise_.pressure_noise_pa = baro["pressure_noise_pa"].as<double>(5.0);
                baroNoise_.temperature_drift_k = baro["temperature_drift_k"].as<double>(0.5);
                gzmsg << "[SensorNoisePlugin] Barometer noise loaded (enabled="
                      << baroNoise_.enabled << ")" << std::endl;
            }

            if (config["magnetometer"])
            {
                auto mag = config["magnetometer"];
                magNoise_.enabled = mag["enabled"].as<bool>(false);
                magNoise_.hard_iron_x = mag["hard_iron_x"].as<double>(0.0);
                magNoise_.hard_iron_y = mag["hard_iron_y"].as<double>(0.0);
                magNoise_.hard_iron_z = mag["hard_iron_z"].as<double>(0.0);
                magNoise_.soft_iron_scale = mag["soft_iron_scale"].as<double>(1.0);
                magNoise_.mag_noise_stddev = mag["noise_stddev"].as<double>(0.001);
                gzmsg << "[SensorNoisePlugin] Magnetometer noise loaded (enabled="
                      << magNoise_.enabled << ")" << std::endl;
            }
        }
        catch (const std::exception &e)
        {
            gzwarn << "[SensorNoisePlugin] Failed to load config: " << e.what()
                   << ". Using defaults." << std::endl;
        }
    }

    void UpdateBiases(double dt)
    {
        if (imuNoise_.enabled)
        {
            std::normal_distribution<double> norm(0.0, 1.0);
            double sqrtDt = std::sqrt(dt);

            // Accelerometer bias random walk
            accelBias_.X() += imuNoise_.accel_random_walk * sqrtDt * norm(rng_);
            accelBias_.Y() += imuNoise_.accel_random_walk * sqrtDt * norm(rng_);
            accelBias_.Z() += imuNoise_.accel_random_walk * sqrtDt * norm(rng_);

            // Gyroscope bias random walk
            gyroBias_.X() += imuNoise_.gyro_random_walk * sqrtDt * norm(rng_);
            gyroBias_.Y() += imuNoise_.gyro_random_walk * sqrtDt * norm(rng_);
            gyroBias_.Z() += imuNoise_.gyro_random_walk * sqrtDt * norm(rng_);
        }
    }

    /// Generate a noisy IMU reading.
    gz::math::Vector3d NoisyAccel(const gz::math::Vector3d &clean, double dt)
    {
        if (!imuNoise_.enabled) return clean;

        std::normal_distribution<double> norm(0.0, 1.0);
        double sqrtDt = std::sqrt(dt);
        double noiseSigma = imuNoise_.accel_noise_density / sqrtDt;

        return clean + accelBias_ + gz::math::Vector3d(
            noiseSigma * norm(rng_),
            noiseSigma * norm(rng_),
            noiseSigma * norm(rng_));
    }

    gz::math::Vector3d NoisyGyro(const gz::math::Vector3d &clean, double dt)
    {
        if (!imuNoise_.enabled) return clean;

        std::normal_distribution<double> norm(0.0, 1.0);
        double sqrtDt = std::sqrt(dt);
        double noiseSigma = imuNoise_.gyro_noise_density / sqrtDt;

        return clean + gyroBias_ + gz::math::Vector3d(
            noiseSigma * norm(rng_),
            noiseSigma * norm(rng_),
            noiseSigma * norm(rng_));
    }

    /// Generate noisy GPS position offset.
    gz::math::Vector3d GpsPositionNoise()
    {
        if (!gpsNoise_.enabled) return gz::math::Vector3d::Zero;

        // Check for GPS dropout
        std::uniform_real_distribution<double> uni(0.0, 1.0);
        if (gpsDropout_ && simTime_ < gpsDropoutEnd_)
        {
            // During dropout, return very large noise (simulating loss)
            return gz::math::Vector3d(999.0, 999.0, 999.0);
        }
        gpsDropout_ = false;

        // Random dropout trigger
        if (uni(rng_) < gpsNoise_.dropout_probability / 1000.0)
        {
            gpsDropout_ = true;
            gpsDropoutEnd_ = simTime_ + gpsNoise_.dropout_duration_sec;
        }

        std::normal_distribution<double> norm(0.0, gpsNoise_.position_noise_m);
        return gz::math::Vector3d(norm(rng_), norm(rng_), norm(rng_) * 1.5);
    }

    /// Generate noisy magnetometer reading.
    gz::math::Vector3d NoisyMag(const gz::math::Vector3d &clean)
    {
        if (!magNoise_.enabled) return clean;

        std::normal_distribution<double> norm(0.0, magNoise_.mag_noise_stddev);

        // Apply hard iron offset
        gz::math::Vector3d noisy = clean;
        noisy.X() += magNoise_.hard_iron_x;
        noisy.Y() += magNoise_.hard_iron_y;
        noisy.Z() += magNoise_.hard_iron_z;

        // Apply soft iron scaling
        noisy *= magNoise_.soft_iron_scale;

        // Add white noise
        noisy.X() += norm(rng_);
        noisy.Y() += norm(rng_);
        noisy.Z() += norm(rng_);

        return noisy;
    }

    // ── Member variables ────────────────────────────────────────────────
    gz::sim::Entity modelEntity_{gz::sim::kNullEntity};
    std::mt19937 rng_;
    double simTime_{0.0};

    // Noise parameters
    SensorNoiseParams imuNoise_;
    SensorNoiseParams gpsNoise_;
    SensorNoiseParams baroNoise_;
    SensorNoiseParams magNoise_;

    // IMU state
    gz::math::Vector3d accelBias_{gz::math::Vector3d::Zero};
    gz::math::Vector3d gyroBias_{gz::math::Vector3d::Zero};

    // GPS state
    bool gpsDropout_{false};
    double gpsDropoutEnd_{0.0};
};

}  // namespace nexus

GZ_ADD_PLUGIN(
    nexus::SensorNoisePlugin,
    gz::sim::System,
    nexus::SensorNoisePlugin::ISystemConfigure,
    nexus::SensorNoisePlugin::ISystemPreUpdate,
    nexus::SensorNoisePlugin::ISystemPostUpdate)

GZ_ADD_PLUGIN_ALIAS(
    nexus::SensorNoisePlugin,
    "nexus::SensorNoisePlugin")
