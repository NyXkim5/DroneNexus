/*
 * wind_plugin.cc
 *
 * Gazebo Garden system plugin that applies wind disturbance forces
 * to drone models. Supports configurable wind speed, direction, gusts,
 * and turbulence (Dryden wind model).
 *
 * Usage in SDF:
 *   <plugin filename="WindPlugin"
 *          name="nexus::WindPlugin">
 *     <config_path>/config/wind.yaml</config_path>
 *     <link_name>base_link</link_name>
 *   </plugin>
 */

#include <random>
#include <string>
#include <memory>
#include <cmath>

#include <gz/sim/System.hh>
#include <gz/sim/Model.hh>
#include <gz/sim/Link.hh>
#include <gz/sim/Util.hh>
#include <gz/sim/components/Link.hh>
#include <gz/sim/components/Name.hh>
#include <gz/sim/components/Pose.hh>
#include <gz/sim/components/ExternalWorldWrenchCmd.hh>
#include <gz/plugin/Register.hh>
#include <gz/common/Console.hh>
#include <gz/math/Vector3.hh>
#include <gz/msgs/wrench.pb.h>

#include <yaml-cpp/yaml.h>

namespace nexus
{

/// Wind configuration parameters.
struct WindConfig
{
    // Mean wind
    double mean_speed_ms{0.0};           // m/s
    double mean_direction_deg{0.0};      // degrees from North (CW)
    double mean_vertical_ms{0.0};        // vertical component

    // Gust parameters
    bool gusts_enabled{false};
    double gust_speed_ms{0.0};           // additional speed during gust
    double gust_duration_sec{2.0};
    double gust_interval_mean_sec{30.0}; // mean time between gusts
    double gust_direction_variance_deg{30.0};

    // Turbulence (Dryden model parameters)
    bool turbulence_enabled{false};
    double turbulence_intensity{0.0};    // 0.0 = calm, 1.0 = severe
    double wind_20ft_ms{5.0};           // wind speed at 20ft (for Dryden)
    double length_scale_m{200.0};        // turbulence length scale

    // Altitude effects
    double surface_roughness{0.01};      // terrain roughness (z0)
    double reference_altitude_m{10.0};   // altitude for mean_speed
    bool altitude_scaling{true};         // scale wind with altitude

    // Drag coefficient (for the model the wind acts on)
    double drag_coefficient{1.0};
    double frontal_area_m2{0.1};
};

class WindPlugin :
    public gz::sim::System,
    public gz::sim::ISystemConfigure,
    public gz::sim::ISystemPreUpdate
{
public:
    WindPlugin() = default;
    ~WindPlugin() override = default;

    // ── ISystemConfigure ────────────────────────────────────────────────
    void Configure(
        const gz::sim::Entity &_entity,
        const std::shared_ptr<const sdf::Element> &_sdf,
        gz::sim::EntityComponentManager &_ecm,
        gz::sim::EventManager &_eventMgr) override
    {
        gzmsg << "[WindPlugin] Configuring..." << std::endl;

        model_ = gz::sim::Model(_entity);

        // Read link name
        linkName_ = "base_link";
        if (_sdf->HasElement("link_name"))
        {
            linkName_ = _sdf->Get<std::string>("link_name");
        }

        // Read config path
        std::string configPath = "/config/wind.yaml";
        if (_sdf->HasElement("config_path"))
        {
            configPath = _sdf->Get<std::string>("config_path");
        }

        // Load from SDF inline parameters as fallbacks
        if (_sdf->HasElement("mean_speed"))
            config_.mean_speed_ms = _sdf->Get<double>("mean_speed");
        if (_sdf->HasElement("mean_direction"))
            config_.mean_direction_deg = _sdf->Get<double>("mean_direction");
        if (_sdf->HasElement("gust_speed"))
            config_.gust_speed_ms = _sdf->Get<double>("gust_speed");
        if (_sdf->HasElement("turbulence_intensity"))
            config_.turbulence_intensity = _sdf->Get<double>("turbulence_intensity");

        LoadConfig(configPath);

        // Initialize RNG
        std::random_device rd;
        rng_ = std::mt19937(rd());

        // Schedule first gust
        if (config_.gusts_enabled)
        {
            std::exponential_distribution<double> exp(1.0 / config_.gust_interval_mean_sec);
            nextGustTime_ = exp(rng_);
        }

        gzmsg << "[WindPlugin] Mean wind: " << config_.mean_speed_ms
              << " m/s @ " << config_.mean_direction_deg << " deg" << std::endl;
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

        // Find link entity (lazy initialization)
        if (linkEntity_ == gz::sim::kNullEntity)
        {
            linkEntity_ = model_.LinkByName(_ecm, linkName_);
            if (linkEntity_ == gz::sim::kNullEntity)
            {
                return;
            }
            gzmsg << "[WindPlugin] Found link: " << linkName_ << std::endl;
        }

        // Get model altitude for wind scaling
        double altitude = 10.0;
        auto poseComp = _ecm.Component<gz::sim::components::Pose>(linkEntity_);
        if (poseComp)
        {
            altitude = std::max(0.1, poseComp->Data().Pos().Z());
        }

        // Compute wind force
        gz::math::Vector3d windForce = ComputeWindForce(altitude, dt);

        // Apply force to link as external wrench
        auto wrenchComp = _ecm.Component<gz::sim::components::ExternalWorldWrenchCmd>(
            linkEntity_);

        gz::msgs::Wrench wrenchMsg;
        wrenchMsg.mutable_force()->set_x(windForce.X());
        wrenchMsg.mutable_force()->set_y(windForce.Y());
        wrenchMsg.mutable_force()->set_z(windForce.Z());

        if (wrenchComp)
        {
            *wrenchComp = gz::sim::components::ExternalWorldWrenchCmd(wrenchMsg);
        }
        else
        {
            _ecm.CreateComponent(
                linkEntity_,
                gz::sim::components::ExternalWorldWrenchCmd(wrenchMsg));
        }
    }

private:
    void LoadConfig(const std::string &path)
    {
        try
        {
            YAML::Node config = YAML::LoadFile(path);

            if (config["wind"])
            {
                auto w = config["wind"];
                config_.mean_speed_ms = w["mean_speed_ms"].as<double>(config_.mean_speed_ms);
                config_.mean_direction_deg = w["mean_direction_deg"].as<double>(config_.mean_direction_deg);
                config_.mean_vertical_ms = w["mean_vertical_ms"].as<double>(0.0);

                if (w["gusts"])
                {
                    config_.gusts_enabled = w["gusts"]["enabled"].as<bool>(false);
                    config_.gust_speed_ms = w["gusts"]["speed_ms"].as<double>(5.0);
                    config_.gust_duration_sec = w["gusts"]["duration_sec"].as<double>(2.0);
                    config_.gust_interval_mean_sec = w["gusts"]["interval_mean_sec"].as<double>(30.0);
                    config_.gust_direction_variance_deg = w["gusts"]["direction_variance_deg"].as<double>(30.0);
                }

                if (w["turbulence"])
                {
                    config_.turbulence_enabled = w["turbulence"]["enabled"].as<bool>(false);
                    config_.turbulence_intensity = w["turbulence"]["intensity"].as<double>(0.1);
                    config_.length_scale_m = w["turbulence"]["length_scale_m"].as<double>(200.0);
                }

                config_.drag_coefficient = w["drag_coefficient"].as<double>(1.0);
                config_.frontal_area_m2 = w["frontal_area_m2"].as<double>(0.1);
                config_.altitude_scaling = w["altitude_scaling"].as<bool>(true);
                config_.surface_roughness = w["surface_roughness"].as<double>(0.01);
            }

            gzmsg << "[WindPlugin] Config loaded from " << path << std::endl;
        }
        catch (const std::exception &e)
        {
            gzwarn << "[WindPlugin] Could not load config: " << e.what()
                   << ". Using defaults/SDF params." << std::endl;
        }
    }

    gz::math::Vector3d ComputeWindForce(double altitude, double dt)
    {
        // ── Mean wind with altitude scaling ─────────────────────────
        double scaledSpeed = config_.mean_speed_ms;
        if (config_.altitude_scaling && altitude > 0.1)
        {
            // Logarithmic wind profile: V(z) = V_ref * ln(z/z0) / ln(z_ref/z0)
            double z0 = config_.surface_roughness;
            double zRef = config_.reference_altitude_m;
            double logRatio = std::log(altitude / z0) / std::log(zRef / z0);
            scaledSpeed = config_.mean_speed_ms * std::max(0.0, logRatio);
        }

        // Wind direction (meteorological: from direction, convert to force direction)
        double dirRad = (config_.mean_direction_deg + 180.0) * M_PI / 180.0;
        double wx = scaledSpeed * std::cos(dirRad);
        double wy = scaledSpeed * std::sin(dirRad);
        double wz = config_.mean_vertical_ms;

        // ── Gusts ───────────────────────────────────────────────────
        if (config_.gusts_enabled)
        {
            if (!gustActive_ && simTime_ >= nextGustTime_)
            {
                // Start a gust
                gustActive_ = true;
                gustEndTime_ = simTime_ + config_.gust_duration_sec;

                std::normal_distribution<double> dirNorm(0.0, config_.gust_direction_variance_deg);
                gustDirection_ = dirRad + dirNorm(rng_) * M_PI / 180.0;

                std::uniform_real_distribution<double> speedVar(0.5, 1.0);
                gustMagnitude_ = config_.gust_speed_ms * speedVar(rng_);
            }

            if (gustActive_)
            {
                if (simTime_ < gustEndTime_)
                {
                    // Gust envelope: ramp up, sustain, ramp down
                    double gustDuration = config_.gust_duration_sec;
                    double elapsed = simTime_ - (gustEndTime_ - gustDuration);
                    double envelope = 1.0;

                    double rampFrac = 0.2;  // 20% ramp up/down
                    if (elapsed < gustDuration * rampFrac)
                        envelope = elapsed / (gustDuration * rampFrac);
                    else if (elapsed > gustDuration * (1.0 - rampFrac))
                        envelope = (gustDuration - elapsed) / (gustDuration * rampFrac);

                    wx += gustMagnitude_ * envelope * std::cos(gustDirection_);
                    wy += gustMagnitude_ * envelope * std::sin(gustDirection_);
                }
                else
                {
                    gustActive_ = false;
                    std::exponential_distribution<double> exp(1.0 / config_.gust_interval_mean_sec);
                    nextGustTime_ = simTime_ + exp(rng_);
                }
            }
        }

        // ── Turbulence (simplified Dryden) ──────────────────────────
        if (config_.turbulence_enabled && config_.turbulence_intensity > 0.0)
        {
            std::normal_distribution<double> norm(0.0, 1.0);
            double sigma = config_.turbulence_intensity * scaledSpeed * 0.5;
            double tau = config_.length_scale_m / std::max(1.0, scaledSpeed);

            // First-order Gauss-Markov process
            double alpha = std::exp(-dt / tau);
            turbU_ = alpha * turbU_ + sigma * std::sqrt(1.0 - alpha * alpha) * norm(rng_);
            turbV_ = alpha * turbV_ + sigma * std::sqrt(1.0 - alpha * alpha) * norm(rng_);
            turbW_ = alpha * turbW_ + sigma * 0.5 * std::sqrt(1.0 - alpha * alpha) * norm(rng_);

            wx += turbU_;
            wy += turbV_;
            wz += turbW_;
        }

        // ── Convert wind velocity to aerodynamic drag force ─────────
        // F = 0.5 * rho * Cd * A * V^2 * direction
        double rho = 1.225;  // air density at sea level [kg/m^3]
        double speed = std::sqrt(wx * wx + wy * wy + wz * wz);
        double forceMag = 0.5 * rho * config_.drag_coefficient *
                         config_.frontal_area_m2 * speed * speed;

        gz::math::Vector3d force(0, 0, 0);
        if (speed > 0.001)
        {
            force.X() = forceMag * (wx / speed);
            force.Y() = forceMag * (wy / speed);
            force.Z() = forceMag * (wz / speed);
        }

        return force;
    }

    // ── Member variables ────────────────────────────────────────────────
    gz::sim::Model model_{gz::sim::kNullEntity};
    gz::sim::Entity linkEntity_{gz::sim::kNullEntity};
    std::string linkName_;
    std::mt19937 rng_;
    double simTime_{0.0};

    WindConfig config_;

    // Gust state
    bool gustActive_{false};
    double gustEndTime_{0.0};
    double nextGustTime_{100.0};
    double gustDirection_{0.0};
    double gustMagnitude_{0.0};

    // Turbulence state (Dryden filter)
    double turbU_{0.0};
    double turbV_{0.0};
    double turbW_{0.0};
};

}  // namespace nexus

GZ_ADD_PLUGIN(
    nexus::WindPlugin,
    gz::sim::System,
    nexus::WindPlugin::ISystemConfigure,
    nexus::WindPlugin::ISystemPreUpdate)

GZ_ADD_PLUGIN_ALIAS(
    nexus::WindPlugin,
    "nexus::WindPlugin")
