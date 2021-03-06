from __future__ import print_function
import math
import time

from pymavlink import mavwp, mavutil

from pysim import util

import sys
import abc
import os

# a list of pexpect objects to read while waiting for
# messages. This keeps the output to stdout flowing
expect_list = []

# get location of scripts
testdir = os.path.dirname(os.path.realpath(__file__))

# Check python version for abstract base class
if sys.version_info[0] >= 3 and sys.version_info[1] >= 4:
        ABC = abc.ABC
else:
    ABC = abc.ABCMeta('ABC', (), {})


class AutoTestTimeoutException(Exception):
    pass


class AutoTest(ABC):
    """Base abstract class.
    It implements the common function for all vehicle types.
    """
    def __init__(self):
        self.mavproxy = None
        self.mav = None

    def progress(self, text):
        """Display autotest progress text."""
        print("AUTOTEST: " + text)

    # following two functions swiped from autotest.py:
    def buildlogs_dirpath(self):
        return os.getenv("BUILDLOGS", util.reltopdir("../buildlogs"))

    def buildlogs_path(self, path):
        '''return a string representing path in the buildlogs directory'''
        bits = [self.buildlogs_dirpath()]
        if isinstance(path, list):
            bits.extend(path)
        else:
            bits.append(path)
        return os.path.join(*bits)

    #################################################
    # GENERAL UTILITIES
    #################################################
    def expect_list_clear(self):
        """clear the expect list."""
        global expect_list
        for p in expect_list[:]:
            expect_list.remove(p)

    def expect_list_extend(self, list_to_add):
        """Extend the expect list."""
        global expect_list
        expect_list.extend(list_to_add)

    def idle_hook(self, mav):
        """Called when waiting for a mavlink message."""
        global expect_list
        for p in expect_list:
            util.pexpect_drain(p)

    def message_hook(self, mav, msg):
        """Called as each mavlink msg is received."""
        self.idle_hook(mav)

    def expect_callback(self, e):
        """Called when waiting for a expect pattern."""
        global expect_list
        for p in expect_list:
            if p == e:
                continue
        util.pexpect_drain(p)

    #################################################
    # SIM UTILITIES
    #################################################
    def get_sim_time(self):
        """Get SITL time."""
        m = self.mav.recv_match(type='SYSTEM_TIME', blocking=True)
        return m.time_boot_ms * 1.0e-3

    def sim_location(self):
        """Return current simulator location."""
        m = self.mav.recv_match(type='SIMSTATE', blocking=True)
        return mavutil.location(m.lat*1.0e-7,
                                m.lng*1.0e-7,
                                0,
                                math.degrees(m.yaw))

    def save_wp(self):
        """Trigger RC 7 to save waypoint."""
        self.mavproxy.send('rc 7 1000\n')
        self.mav.recv_match(condition='RC_CHANNELS.chan7_raw==1000',
                            blocking=True)
        self.wait_seconds(1)
        self.mavproxy.send('rc 7 2000\n')
        self.mav.recv_match(condition='RC_CHANNELS.chan7_raw==2000',
                            blocking=True)
        self.wait_seconds(1)
        self.mavproxy.send('rc 7 1000\n')
        self.mav.recv_match(condition='RC_CHANNELS.chan7_raw==1000',
                            blocking=True)
        self.wait_seconds(1)

    def log_download(self, filename, timeout=360):
        """Download latest log."""
        self.disarm_vehicle()
        self.mav.wait_heartbeat()
        self.mavproxy.send("log list\n")
        self.mavproxy.expect("numLogs")
        self.mav.wait_heartbeat()
        self.mav.wait_heartbeat()
        self.mavproxy.send("set shownoise 0\n")
        self.mavproxy.send("log download latest %s\n" % filename)
        self.mavproxy.expect("Finished downloading", timeout=timeout)
        self.mav.wait_heartbeat()
        self.mav.wait_heartbeat()
        return True

    def show_gps_and_sim_positions(self, on_off):
        """Allow to display gps and actual position on map."""
        if on_off is True:
            # turn on simulator display of gps and actual position
            self.mavproxy.send('map set showgpspos 1\n')
            self.mavproxy.send('map set showsimpos 1\n')
        else:
            # turn off simulator display of gps and actual position
            self.mavproxy.send('map set showgpspos 0\n')
            self.mavproxy.send('map set showsimpos 0\n')

    def mission_count(self, filename):
        """Load a mission from a file and return number of waypoints."""
        wploader = mavwp.MAVWPLoader()
        wploader.load(filename)
        num_wp = wploader.count()
        return num_wp

    def load_mission_from_file(self, filename):
        """Load a mission from a file to flight controller."""
        self.mavproxy.send('wp load %s\n' % filename)
        self.mavproxy.expect('Flight plan received')
        self.mavproxy.send('wp list\n')
        self.mavproxy.expect('Requesting [0-9]+ waypoints')

        # update num_wp
        wploader = mavwp.MAVWPLoader()
        wploader.load(filename)
        num_wp = wploader.count()
        return num_wp

    def save_mission_to_file(self, filename):
        """Save a mission to a file"""
        self.mavproxy.send('wp save %s\n' % filename)
        self.mavproxy.expect('Saved ([0-9]+) waypoints')
        num_wp = int(self.mavproxy.match.group(1))
        self.progress("num_wp: %d" % num_wp)
        return num_wp

    def set_rc_default(self):
        """Setup all simulated RC control to 1500."""
        for chan in range(1, 16):
            self.mavproxy.send('rc %u 1500\n' % chan)

    def set_rc(self, chan, pwm, timeout=5):
        """Setup a simulated RC control to a PWM value"""
        tstart = self.get_sim_time()
        while self.get_sim_time() < tstart + timeout:
            self.mavproxy.send('rc %u %u\n' % (chan, pwm))
            m = self.mav.recv_match(type='RC_CHANNELS', blocking=True)
            chan_pwm = getattr(m, "chan" + str(chan) + "_raw")
            if chan_pwm == pwm:
                return True
        self.progress("Failed to send RC commands")
        return False

    def arm_vehicle(self):
        """Arm vehicle with mavlink arm message."""
        self.mavproxy.send('arm throttle\n')
        self.mav.motors_armed_wait()
        self.progress("ARMED")
        return True

    def disarm_vehicle(self):
        """Disarm vehicle with mavlink disarm message."""
        self.mavproxy.send('disarm\n')
        self.mav.motors_disarmed_wait()
        self.progress("DISARMED")
        return True

    def set_parameter(self, name, value):
        for i in range(1, 10):
            self.mavproxy.send("param set %s %s\n" % (name, str(value)))
            self.mavproxy.send("param fetch %s\n" % name)
            self.mavproxy.expect("%s = (.*)" % (name,))
            returned_value = self.mavproxy.match.group(1)
            if float(returned_value) == float(value):
                # yes, exactly equal.
                break
            self.progress("Param fetch returned incorrect value (%s) vs (%s)"
                          % (returned_value, value))

    def get_parameter(self, name):
        self.mavproxy.send("param fetch %s\n" % name)
        self.mavproxy.expect("%s = (.*)" % (name,))
        return float(self.mavproxy.match.group(1))

    #################################################
    # UTILITIES
    #################################################
    @staticmethod
    def get_distance(loc1, loc2):
        """Get ground distance between two locations."""
        dlat = loc2.lat - loc1.lat
        dlong = loc2.lng - loc1.lng
        return math.sqrt((dlat*dlat) + (dlong*dlong)) * 1.113195e5

    @staticmethod
    def get_bearing(loc1, loc2):
        """Get bearing from loc1 to loc2."""
        off_x = loc2.lng - loc1.lng
        off_y = loc2.lat - loc1.lat
        bearing = 90.00 + math.atan2(-off_y, off_x) * 57.2957795
        if bearing < 0:
            bearing += 360.00
        return bearing

    def do_get_autopilot_capabilities(self):
        self.mavproxy.send("long REQUEST_AUTOPILOT_CAPABILITIES 1\n")
        m = self.mav.recv_match(type='AUTOPILOT_VERSION',
                                blocking=True,
                                timeout=10)
        if m is None:
            self.progress("AUTOPILOT_VERSION not received")
            return False
        self.progress("AUTOPILOT_VERSION received")
        return True

    def do_set_mode_via_command_long(self):
        base_mode = mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        custom_mode = 4  # hold
        start = time.time()
        while time.time() - start < 5:
            self.mavproxy.send("long DO_SET_MODE %u %u\n" %
                               (base_mode, custom_mode))
            m = self.mav.recv_match(type='HEARTBEAT',
                                    blocking=True,
                                    timeout=10)
            if m is None:
                return False
            if m.custom_mode == custom_mode:
                return True
            time.sleep(0.1)
        return False

    def reach_heading_manual(self, heading):
        """Manually direct the vehicle to the target heading."""
        if self.mav.mav_type in [mavutil.mavlink.MAV_TYPE_QUADROTOR,
                                 mavutil.mavlink.MAV_TYPE_HELICOPTER,
                                 mavutil.mavlink.MAV_TYPE_HEXAROTOR,
                                 mavutil.mavlink.MAV_TYPE_OCTOROTOR,
                                 mavutil.mavlink.MAV_TYPE_COAXIAL,
                                 mavutil.mavlink.MAV_TYPE_TRICOPTER]:
            self.mavproxy.send('rc 4 1580\n')
            if not self.wait_heading(heading):
                self.progress("Failed to reach heading")
                return False
            self.mavproxy.send('rc 4 1500\n')
            self.mav.recv_match(condition='RC_CHANNELS.chan4_raw==1500',
                                blocking=True)
        if self.mav.mav_type == mavutil.mavlink.MAV_TYPE_FIXED_WING:
            self.progress("NOT IMPLEMENTED")
        if self.mav.mav_type == mavutil.mavlink.MAV_TYPE_GROUND_ROVER:
            self.mavproxy.send('rc 1 1700\n')
            self.mavproxy.send('rc 3 1550\n')
            if not self.wait_heading(heading):
                self.progress("Failed to reach heading")
                return False
            self.mavproxy.send('rc 3 1500\n')
            self.mav.recv_match(condition='RC_CHANNELS.chan3_raw==1500',
                                blocking=True)
            self.mavproxy.send('rc 1 1500\n')
            self.mav.recv_match(condition='RC_CHANNELS.chan1_raw==1500',
                                blocking=True)
        return True

    def reach_distance_manual(self,  distance):
        """Manually direct the vehicle to the target distance from home."""
        if self.mav.mav_type in [mavutil.mavlink.MAV_TYPE_QUADROTOR,
                                 mavutil.mavlink.MAV_TYPE_HELICOPTER,
                                 mavutil.mavlink.MAV_TYPE_HEXAROTOR,
                                 mavutil.mavlink.MAV_TYPE_OCTOROTOR,
                                 mavutil.mavlink.MAV_TYPE_COAXIAL,
                                 mavutil.mavlink.MAV_TYPE_TRICOPTER]:
            self.mavproxy.send('rc 2 1350\n')
            if not self.wait_distance(distance, accuracy=5, timeout=60):
                self.progress("Failed to reach distance of %u" % distance)
                return False
            self.mavproxy.send('rc 2 1500\n')
            self.mav.recv_match(condition='RC_CHANNELS.chan2_raw==1500',
                                blocking=True)
        if self.mav.mav_type == mavutil.mavlink.MAV_TYPE_FIXED_WING:
            self.progress("NOT IMPLEMENTED")
        if self.mav.mav_type == mavutil.mavlink.MAV_TYPE_GROUND_ROVER:
            self.mavproxy.send('rc 3 1700\n')
            if not self.wait_distance(distance, accuracy=2):
                self.progress("Failed to reach distance of %u" % distance)
                return False
            self.mavproxy.send('rc 3 1500\n')
            self.mav.recv_match(condition='RC_CHANNELS.chan3_raw==1500',
                                blocking=True)
        return True

    #################################################
    # WAIT UTILITIES
    #################################################
    def wait_seconds(self, seconds_to_wait):
        """Wait some second in SITL time."""
        tstart = self.get_sim_time()
        tnow = tstart
        while tstart + seconds_to_wait > tnow:
            tnow = self.get_sim_time()

    def wait_altitude(self, alt_min, alt_max, timeout=30):
        """Wait for a given altitude range."""
        climb_rate = 0
        previous_alt = 0

        tstart = self.get_sim_time()
        self.progress("Waiting for altitude between %u and %u" %
                      (alt_min, alt_max))
        while self.get_sim_time() < tstart + timeout:
            m = self.mav.recv_match(type='VFR_HUD', blocking=True)
            climb_rate = m.alt - previous_alt
            previous_alt = m.alt
            self.progress("Wait Altitude: Cur:%u, min_alt:%u, climb_rate: %u"
                          % (m.alt, alt_min, climb_rate))
            if m.alt >= alt_min and m.alt <= alt_max:
                self.progress("Altitude OK")
                return True
        self.progress("Failed to attain altitude range")
        return False

    def wait_groundspeed(self, gs_min, gs_max, timeout=30):
        """Wait for a given ground speed range."""
        tstart = self.get_sim_time()
        self.progress("Waiting for groundspeed between %.1f and %.1f" %
                      (gs_min, gs_max))
        while self.get_sim_time() < tstart + timeout:
            m = self.mav.recv_match(type='VFR_HUD', blocking=True)
            self.progress("Wait groundspeed %.1f, target:%.1f" %
                          (m.groundspeed, gs_min))
            if m.groundspeed >= gs_min and m.groundspeed <= gs_max:
                return True
        self.progress("Failed to attain groundspeed range")
        return False

    def wait_roll(self, roll, accuracy, timeout=30):
        """Wait for a given roll in degrees."""
        tstart = self.get_sim_time()
        self.progress("Waiting for roll of %d at %s" % (roll, time.ctime()))
        while self.get_sim_time() < tstart + timeout:
            m = self.mav.recv_match(type='ATTITUDE', blocking=True)
            p = math.degrees(m.pitch)
            r = math.degrees(m.roll)
            self.progress("Roll %d Pitch %d" % (r, p))
            if math.fabs(r - roll) <= accuracy:
                self.progress("Attained roll %d" % roll)
                return True
        self.progress("Failed to attain roll %d" % roll)
        return False

    def wait_pitch(self, pitch, accuracy, timeout=30):
        """Wait for a given pitch in degrees."""
        tstart = self.get_sim_time()
        self.progress("Waiting for pitch of %u at %s" % (pitch, time.ctime()))
        while self.get_sim_time() < tstart + timeout:
            m = self.mav.recv_match(type='ATTITUDE', blocking=True)
            p = math.degrees(m.pitch)
            r = math.degrees(m.roll)
            self.progress("Pitch %d Roll %d" % (p, r))
            if math.fabs(p - pitch) <= accuracy:
                self.progress("Attained pitch %d" % pitch)
                return True
        self.progress("Failed to attain pitch %d" % pitch)
        return False

    def wait_heading(self, heading, accuracy=5, timeout=30):
        """Wait for a given heading."""
        tstart = self.get_sim_time()
        self.progress("Waiting for heading %u with accuracy %u" %
                      (heading, accuracy))
        while self.get_sim_time() < tstart + timeout:
            m = self.mav.recv_match(type='VFR_HUD', blocking=True)
            self.progress("Heading %u" % m.heading)
            if math.fabs(m.heading - heading) <= accuracy:
                self.progress("Attained heading %u" % heading)
                return True
        self.progress("Failed to attain heading %u" % heading)
        return False

    def wait_distance(self, distance, accuracy=5, timeout=30):
        """Wait for flight of a given distance."""
        tstart = self.get_sim_time()
        start = self.mav.location()
        while self.get_sim_time() < tstart + timeout:
            pos = self.mav.location()
            delta = self.get_distance(start, pos)
            self.progress("Distance %.2f meters" % delta)
            if math.fabs(delta - distance) <= accuracy:
                self.progress("Attained distance %.2f meters OK" % delta)
                return True
            if delta > (distance + accuracy):
                self.progress("Failed distance - overshoot delta=%f dist=%f"
                              % (delta, distance))
                return False
        self.progress("Failed to attain distance %u" % distance)
        return False

    def wait_location(self,
                      loc,
                      accuracy=5,
                      timeout=30,
                      target_altitude=None,
                      height_accuracy=-1):
        """Wait for arrival at a location."""
        tstart = self.get_sim_time()
        if target_altitude is None:
            target_altitude = loc.alt
        self.progress("Waiting for location"
                      "%.4f,%.4f at altitude %.1f height_accuracy=%.1f" %
                      (loc.lat, loc.lng, target_altitude, height_accuracy))
        while self.get_sim_time() < tstart + timeout:
            pos = self.mav.location()
            delta = self.get_distance(loc, pos)
            self.progress("Distance %.2f meters alt %.1f" % (delta, pos.alt))
            if delta <= accuracy:
                height_delta = math.fabs(pos.alt - target_altitude)
                if (height_accuracy != -1 and height_delta > height_accuracy):
                    continue
                self.progress("Reached location (%.2f meters)" % delta)
                return True
        self.progress("Failed to attain location")
        return False

    def wait_waypoint(self,
                      wpnum_start,
                      wpnum_end,
                      allow_skip=True,
                      max_dist=2,
                      timeout=400):
        """Wait for waypoint ranges."""
        tstart = self.get_sim_time()
        # this message arrives after we set the current WP
        start_wp = self.mav.waypoint_current()
        current_wp = start_wp
        mode = self.mav.flightmode

        self.progress("\ntest: wait for waypoint ranges start=%u end=%u\n\n"
                      % (wpnum_start, wpnum_end))
        # if start_wp != wpnum_start:
        #    self.progress("test: Expected start waypoint %u but got %u" %
        #                  (wpnum_start, start_wp))
        #    return False

        while self.get_sim_time() < tstart + timeout:
            seq = self.mav.waypoint_current()
            m = self.mav.recv_match(type='NAV_CONTROLLER_OUTPUT',
                                    blocking=True)
            wp_dist = m.wp_dist
            m = self.mav.recv_match(type='VFR_HUD', blocking=True)

            # if we changed mode, fail
            if self.mav.flightmode != mode:
                self.progress('Exited %s mode' % mode)
                return False

            self.progress("test: WP %u (wp_dist=%u Alt=%d), current_wp: %u,"
                          "wpnum_end: %u" %
                          (seq, wp_dist, m.alt, current_wp, wpnum_end))
            if seq == current_wp+1 or (seq > current_wp+1 and allow_skip):
                self.progress("test: Starting new waypoint %u" % seq)
                tstart = self.get_sim_time()
                current_wp = seq
                # the wp_dist check is a hack until we can sort out
                # the right seqnum for end of mission
            # if current_wp == wpnum_end or (current_wp == wpnum_end-1 and
            #                                wp_dist < 2):
            if current_wp == wpnum_end and wp_dist < max_dist:
                self.progress("Reached final waypoint %u" % seq)
                return True
            if seq >= 255:
                self.progress("Reached final waypoint %u" % seq)
                return True
            if seq > current_wp+1:
                self.progress("Failed: Skipped waypoint! Got wp %u expected %u"
                              % (seq, current_wp+1))
                return False
        self.progress("Failed: Timed out waiting for waypoint %u of %u" %
                      (wpnum_end, wpnum_end))
        return False

    def wait_mode(self, mode, timeout=None):
        """Wait for mode to change."""
        self.progress("Waiting for mode %s" % mode)
        tstart = self.get_sim_time()
        hastimeout = False
        while self.mav.flightmode.upper() != mode.upper() and not hastimeout:
            if timeout is not None:
                hastimeout = self.get_sim_time() > tstart + timeout
            self.mav.wait_heartbeat()
        self.progress("Got mode %s" % mode)
        return self.mav.flightmode

    def wait_ready_to_arm(self, timeout=None):
        # wait for EKF checks to pass
        return self.wait_ekf_happy(timeout=timeout)

    def wait_ekf_happy(self, timeout=30):
        """Wait for EKF to be happy"""

        tstart = self.get_sim_time()
        required_value = 831
        self.progress("Waiting for EKF value %u" % required_value)
        while timeout is None or self.get_sim_time() < tstart + timeout:
            m = self.mav.recv_match(type='EKF_STATUS_REPORT', blocking=True)
            current = m.flags
            if (tstart - self.get_sim_time()) % 5 == 0:
                self.progress("Wait EKF.flags: required:%u current:%u" %
                              (required_value, current))
            if current == required_value:
                self.progress("EKF Flags OK")
                return
        self.progress("Failed to get EKF.flags=%u" % required_value)
        raise AutoTestTimeoutException()

    @abc.abstractmethod
    def init(self):
        """Initilialize autotest feature."""
        pass

    # def test_common_feature(self):
    #     """Common feature to test."""
    #     sucess = True
    #     # TEST ARMING/DISARM
    #     if not self.arm_vehicle():
    #         self.progress("Failed to ARM")
    #         sucess = False
    #     if not self.disarm_vehicle():
    #         self.progress("Failed to DISARM")
    #         sucess = False
    #     if not self.test_arm_motors_radio():
    #         self.progress("Failed to ARM with radio")
    #         sucess = False
    #     if not self.test_disarm_motors_radio():
    #         self.progress("Failed to ARM with radio")
    #         sucess = False
    #     if not self.test_autodisarm_motors():
    #         self.progress("Failed to AUTO DISARM")
    #         sucess = False
    #     # TODO: Test failure on arm (with arming check)
    #     # TEST MISSION FILE
    #     # TODO : rework that to work on autotest server
    #     # self.progress("TEST LOADING MISSION")
    #     # num_wp = self.load_mission_from_file(
    #                  os.path.join(testdir, "fake_mission.txt"))
    #     # if num_wp == 0:
    #     #     self.progress("Failed to load all_msg_mission")
    #     #     sucess = False
    #     #
    #     # self.progress("TEST SAVING MISSION")
    #     # num_wp_old = num_wp
    #     # num_wp = self.save_mission_to_file(os.path.join(testdir,
    #                                          "fake_mission2.txt"))
    #     # if num_wp != num_wp_old:
    #     #     self.progress("Failed to save all_msg_mission")
    #     #     sucess = False
    #
    #     self.progress("TEST CLEARING MISSION")
    #     self.mavproxy.send("wp clear\n")
    #     self.mavproxy.send('wp list\n')
    #     self.mavproxy.expect('Requesting [0-9]+ waypoints')
    #     num_wp = mavwp.MAVWPLoader().count()
    #     if num_wp != 0:
    #         self.progress("Failed to clear mission ")
    #         sucess = False
    #
    #     return sucess
    #
    # # TESTS FAILSAFE
    # @abc.abstractmethod
    # def test_throttle_failsafe(self, home, distance_min=10, side=60,
    #                            timeout=180):
    #     """Test that RTL success in case of thottle failsafe."""
    #     pass
    #
    # # TEST ARM RADIO
    # @abc.abstractmethod
    # def test_arm_motors_radio(self):
    #     """Test arming with RC sticks."""
    #     pass
    #
    # # TEST DISARM RADIO
    # @abc.abstractmethod
    # def test_disarm_motors_radio(self):
    #     """Test disarming with RC sticks."""
    #     pass
    #
    # # TEST AUTO DISARM
    # @abc.abstractmethod
    # def test_autodisarm_motors(self):
    #     """Test auto disarming."""
    #     pass
    #
    # # TEST RC OVERRIDE
    # # TEST RC OVERRIDE TIMEOUT
    # @abc.abstractmethod
    # def test_rtl(self, home, distance_min=10, timeout=250):
    #     """Test that RTL success."""
    #     self.progress("# Enter RTL")
    #     self.mavproxy.send('switch 3\n')
    #     tstart = self.get_sim_time()
    #     while self.get_sim_time() < tstart + timeout:
    #         m = self.mav.recv_match(type='VFR_HUD', blocking=True)
    #         pos = self.mav.location()
    #         home_distance = self.get_distance(home, pos)
    #         self.progress("Alt: %u  HomeDistance: %.0f" %
    #                        (m.alt, home_distance))
    #         if m.alt <= 1 and home_distance < distance_min:
    #             self.progress("RTL Complete")
    #             return True
    #     return False
    #
    # @abc.abstractmethod
    # def test_mission(self, filename):
    #     pass

    @abc.abstractmethod
    def autotest(self):
        """Autotest used by ArduPilot autotest CI."""
        pass
