#!/usr/bin/env python
from __future__ import print_function

'''
Joinmarket GUI using PyQt for doing coinjoins.
Some widgets copied and modified from https://github.com/spesmilo/electrum


    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

import sys, base64, textwrap, datetime, os, logging
import platform, csv, threading, time

from decimal import Decimal
from functools import partial

from PyQt4 import QtCore
from PyQt4.QtGui import *

if platform.system() == 'Windows':
    MONOSPACE_FONT = 'Lucida Console'
elif platform.system() == 'Darwin':
    MONOSPACE_FONT = 'Monaco'
else:
    MONOSPACE_FONT = 'monospace'

import jmbitcoin as btc

#General Joinmarket donation address; TODO
donation_address = "1AZgQZWYRteh6UyF87hwuvyWj73NvWKpL"

JM_CORE_VERSION = '0.2.2'
JM_GUI_VERSION = '5'

from jmclient import (load_program_config, get_network, Wallet,
                      get_p2pk_vbyte, jm_single, validate_address,
                      get_log, weighted_order_choose, Taker,
                      JMTakerClientProtocolFactory, WalletError,
                      start_reactor, get_schedule, get_tumble_schedule,
                      schedule_to_text, mn_decode, mn_encode, create_wallet_file,
                      get_blockchain_interface_instance, sync_wallet, direct_send,
                      RegtestBitcoinCoreInterface, tweak_tumble_schedule,
                      human_readable_schedule_entry, tumbler_taker_finished_update,
                      get_tumble_log, restart_wait, tumbler_filter_orders_callback)

from qtsupport import (ScheduleWizard, TumbleRestartWizard, warnings, config_tips,
                       config_types, TaskThread, QtHandler, XStream, Buttons,
                       CloseButton, CopyButton, CopyCloseButton, OkButton,
                       CancelButton, check_password_strength,
                       update_password_strength, make_password_dialog,
                       PasswordDialog, MyTreeWidget, JMQtMessageBox, BLUE_FG,
                       donation_more_message)

def satoshis_to_amt_str(x):
    return str(Decimal(x)/Decimal('1e8')) + " BTC"

log = get_log()

def update_config_for_gui():
    '''The default joinmarket config does not contain these GUI settings
    (they are generally set by command line flags or not needed).
    If they are set in the file, use them, else set the defaults.
    These *will* be persisted to joinmarket.cfg, but that will not affect
    operation of the command line version.
    '''
    gui_config_names = ['gaplimit', 'history_file', 'check_high_fee',
                        'max_mix_depth', 'txfee_default', 'order_wait_time',
                        'daemon_port', 'checktx']
    gui_config_default_vals = ['6', 'jm-tx-history.txt', '2', '5', '5000', '30',
                               '27183', 'true']
    if "GUI" not in jm_single().config.sections():
        jm_single().config.add_section("GUI")
    gui_items = jm_single().config.items("GUI")
    for gcn, gcv in zip(gui_config_names, gui_config_default_vals):
        if gcn not in [_[0] for _ in gui_items]:
            jm_single().config.set("GUI", gcn, gcv)
    #Extra setting not exposed to the GUI, but only for the GUI app
    if 'privacy_warning' not in [_[0] for _ in gui_items]:
        print('overwriting privacy_warning')
        jm_single().config.set("GUI", 'privacy_warning', '1')


def persist_config():
    '''This loses all comments in the config file.
    TODO: possibly correct that.'''
    with open('joinmarket.cfg', 'w') as f:
        jm_single().config.write(f)

def checkAddress(parent, addr):
    valid, errmsg = validate_address(str(addr))
    if not valid:
        JMQtMessageBox(parent,
                       "Bitcoin address not valid.\n" + errmsg,
                       mbtype='warn',
                       title="Error")

def getSettingsWidgets():
    results = []
    sN = ['Recipient address', 'Number of counterparties', 'Mixdepth',
          'Amount in bitcoins (BTC)']
    sH = ['The address you want to send the payment to',
          'How many other parties to send to; if you enter 4\n' +
          ', there will be 5 participants, including you.\n' +
          'Enter 0 to send direct without coinjoin.',
          'The mixdepth of the wallet to send the payment from',
          'The amount IN BITCOINS to send.\n' +
          'If you enter 0, a SWEEP transaction\nwill be performed,' +
          ' spending all the coins \nin the given mixdepth.']
    sT = [str, int, int, float]
    #todo maxmixdepth
    sMM = ['', (2, 20),
           (0, jm_single().config.getint("GUI", "max_mix_depth") - 1),
           (0.00000001, 100.0, 8)]
    sD = ['', '3', '0', '']
    for x in zip(sN, sH, sT, sD, sMM):
        ql = QLabel(x[0])
        ql.setToolTip(x[1])
        qle = QLineEdit(x[3])
        if x[2] == int:
            qle.setValidator(QIntValidator(*x[4]))
        if x[2] == float:
            qle.setValidator(QDoubleValidator(*x[4]))
        results.append((ql, qle))
    return results


handler = QtHandler()
handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
log.addHandler(handler)

class HelpLabel(QLabel):

    def __init__(self, text, help_text, wtitle):
        QLabel.__init__(self, text)
        self.help_text = help_text
        self.wtitle = wtitle
        self.font = QFont()
        self.setStyleSheet(BLUE_FG)

    def mouseReleaseEvent(self, x):
        QMessageBox.information(w, self.wtitle, self.help_text, 'OK')

    def enterEvent(self, event):
        self.font.setUnderline(True)
        self.setFont(self.font)
        app.setOverrideCursor(QCursor(QtCore.Qt.PointingHandCursor))
        return QLabel.enterEvent(self, event)

    def leaveEvent(self, event):
        self.font.setUnderline(False)
        self.setFont(self.font)
        app.setOverrideCursor(QCursor(QtCore.Qt.ArrowCursor))
        return QLabel.leaveEvent(self, event)

class SettingsTab(QDialog):

    def __init__(self):
        super(SettingsTab, self).__init__()
        self.initUI()

    def initUI(self):
        outerGrid = QGridLayout()
        sA = QScrollArea()
        sA.setWidgetResizable(True)
        frame = QFrame()
        grid = QGridLayout()
        self.settingsFields = []
        j = 0
        for i, section in enumerate(jm_single().config.sections()):
            pairs = jm_single().config.items(section)
            #an awkward design element from the core code: maker_timeout_sec
            #is set outside the config, if it doesn't exist in the config.
            #Add it here and it will be in the newly updated config file.
            if section == 'TIMEOUT' and 'maker_timeout_sec' not in [
                    _[0] for _ in pairs
            ]:
                jm_single().config.set(section, 'maker_timeout_sec', '60')
                pairs = jm_single().config.items(section)
            newSettingsFields = self.getSettingsFields(section,
                                                       [_[0] for _ in pairs])
            self.settingsFields.extend(newSettingsFields)
            sL = QLabel(section)
            sL.setStyleSheet("QLabel {color: blue;}")
            grid.addWidget(sL)
            j += 1
            for k, ns in enumerate(newSettingsFields):
                grid.addWidget(ns[0], j, 0)
                #try to find the tooltip for this label from config tips;
                #it might not be there
                if str(ns[0].text()) in config_tips:
                    ttS = config_tips[str(ns[0].text())]
                    ns[0].setToolTip(ttS)
                grid.addWidget(ns[1], j, 1)
                sfindex = len(self.settingsFields) - len(newSettingsFields) + k
                if isinstance(ns[1], QCheckBox):
                    ns[1].toggled.connect(lambda checked, s=section,
                                          q=sfindex: self.handleEdit(
                                    s, self.settingsFields[q], checked))
                else:
                    ns[1].editingFinished.connect(
                    lambda q=sfindex, s=section: self.handleEdit(s,
                                                      self.settingsFields[q]))
                j += 1
        outerGrid.addWidget(sA)
        sA.setWidget(frame)
        frame.setLayout(grid)
        frame.adjustSize()
        self.setLayout(outerGrid)
        self.show()

    def handleEdit(self, section, t, checked=None):
        if isinstance(t[1], QCheckBox):
            if str(t[0].text()) == 'Testnet':
                oname = 'network'
                oval = 'testnet' if checked else 'mainnet'
                add = '' if not checked else ' - Testnet'
                w.setWindowTitle(appWindowTitle + add)
            else:
                oname = str(t[0].text())
                oval = 'true' if checked else 'false'
            log.debug('setting section: ' + section + ' and name: ' + oname +
                      ' to: ' + oval)
            jm_single().config.set(section, oname, oval)

        else:  #currently there is only QLineEdit
            log.debug('setting section: ' + section + ' and name: ' + str(t[
                0].text()) + ' to: ' + str(t[1].text()))
            jm_single().config.set(section, str(t[0].text()), str(t[1].text()))
            if str(t[0].text()) == 'blockchain_source':
                jm_single().bc_interface = get_blockchain_interface_instance(
                    jm_single().config)
            if str(t[0].text()) == 'maker_timeout_sec':
                jm_single().maker_timeout_sec = int(t[1].text())
                log.debug("Set maker timeout sec to : " + str(jm_single().maker_timeout_sec))

    def getSettingsFields(self, section, names):
        results = []
        for name in names:
            val = jm_single().config.get(section, name)
            if name in config_types:
                t = config_types[name]
                if t == bool:
                    qt = QCheckBox()
                    if val == 'testnet' or val.lower() == 'true':
                        qt.setChecked(True)
                elif not t:
                    continue
                else:
                    qt = QLineEdit(val)
                    if t == int:
                        qt.setValidator(QIntValidator(0, 65535))
            else:
                qt = QLineEdit(val)
            label = 'Testnet' if name == 'network' else name
            results.append((QLabel(label), qt))
        return results

class SpendStateMgr(object):
    """A primitive class keep track of the mode
    in which the spend tab is being run
    """
    def __init__(self, updatecallback):
        self.reset_vars()
        self.updatecallback = updatecallback

    def updateType(self, t):
        self.typestate = t
        self.updatecallback()

    def updateRun(self, r):
        self.runstate = r
        self.updatecallback()

    def reset_vars(self):
        self.typestate = 'single'
        self.runstate = 'ready'
        self.schedule_name = None
        self.loaded_schedule = None

    def reset(self):
        self.reset_vars()
        self.updatecallback()

class SpendTab(QWidget):

    def __init__(self):
        super(SpendTab, self).__init__()
        self.initUI()
        self.taker = None
        self.filter_offers_response = None
        self.taker_info_response = None
        self.clientfactory = None
        self.tumbler_options = None
        #signals from client backend to GUI
        self.jmclient_obj = QtCore.QObject()
        #timer for waiting for confirmation on restart
        self.restartTimer = QtCore.QTimer()
        #timer for wait for next transaction
        self.nextTxTimer = None
        #This signal/callback requires user acceptance decision.
        self.jmclient_obj.connect(self.jmclient_obj, QtCore.SIGNAL('JMCLIENT:offers'),
                                                            self.checkOffers)
        #This signal/callback is for information only (including abort/error
        #conditions which require no feedback from user.
        self.jmclient_obj.connect(self.jmclient_obj, QtCore.SIGNAL('JMCLIENT:info'),
                                  self.takerInfo)
        #Signal indicating Taker has finished its work
        self.jmclient_obj.connect(self.jmclient_obj, QtCore.SIGNAL('JMCLIENT:finished'),
                                  self.takerFinished)
        #tracks which mode the spend tab is run in
        self.spendstate = SpendStateMgr(self.toggleButtons)
        self.spendstate.reset() #trigger callback to 'ready' state

    def generateTumbleSchedule(self):
        #needs a set of tumbler options and destination addresses, so needs
        #a wizard
        wizard = ScheduleWizard()
        wizard_return = wizard.exec_()
        if wizard_return == QDialog.Rejected:
            return
        self.spendstate.loaded_schedule = wizard.get_schedule()
        self.spendstate.schedule_name = wizard.get_name()
        self.updateSchedView()
        self.tumbler_options = wizard.opts
        self.tumbler_destaddrs = wizard.get_destaddrs()
        #tumbler may require more mixdepths; update the wallet
        required_mixdepths = self.tumbler_options['mixdepthsrc'] + \
            self.tumbler_options['mixdepthcount']
        if required_mixdepths > jm_single().config.getint("GUI", "max_mix_depth"):
            jm_single().config.set("GUI", "max_mix_depth", str(required_mixdepths))
            #recreate wallet and sync again; needed due to cache.
            JMQtMessageBox(self,
            "Max mixdepth has been reset to: " + str(required_mixdepths) + ".\n" +
            "Please choose 'Load' from the Wallet menu and resync before running.",
            title='Wallet resync required')
            return
        self.sch_startButton.setEnabled(True)

    def selectSchedule(self):
        current_path = os.path.dirname(os.path.realpath(__file__))
        firstarg = QFileDialog.getOpenFileName(self,
                                               'Choose Schedule File',
                                               directory=current_path)
        #TODO validate the schedule
        log.debug('Looking for schedule in: ' + firstarg)
        if not firstarg:
            return
        #extract raw text before processing
        with open(firstarg, 'rb') as f:
            rawsched = f.read()

        res, schedule = get_schedule(firstarg)
        if not res:
            JMQtMessageBox(self, "Not a valid JM schedule file", mbtype='crit',
                           title='Error')
        else:
            w.statusBar().showMessage("Schedule loaded OK.")
            self.spendstate.loaded_schedule = schedule
            self.spendstate.schedule_name = os.path.basename(str(firstarg))
            self.updateSchedView()
            if self.spendstate.schedule_name == "TUMBLE.schedule":
                reply = JMQtMessageBox(self, "An incomplete tumble run detected. "
                                       "\nDo you want to restart?",
                                       title="Restart detected", mbtype='question')
                if reply != QMessageBox.Yes:
                    self.giveUp()
                    return
                self.tumbler_options = True

    def updateSchedView(self):
        self.sch_label2.setText(self.spendstate.schedule_name)
        self.sched_view.setText(schedule_to_text(self.spendstate.loaded_schedule))

    def getDonateLayout(self):
        donateLayout = QHBoxLayout()
        self.donateCheckBox = QCheckBox()
        self.donateCheckBox.setChecked(False)
        #Temporarily disabled
        self.donateCheckBox.setEnabled(False)
        self.donateCheckBox.setMaximumWidth(30)
        self.donateLimitBox = QDoubleSpinBox()
        self.donateLimitBox.setMinimum(0.001)
        self.donateLimitBox.setMaximum(0.100)
        self.donateLimitBox.setSingleStep(0.001)
        self.donateLimitBox.setDecimals(3)
        self.donateLimitBox.setValue(0.010)
        self.donateLimitBox.setMaximumWidth(100)
        self.donateLimitBox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        donateLayout.addWidget(self.donateCheckBox)
        label1 = QLabel("Check to send change lower than: ")
        label1.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        donateLayout.addWidget(label1)
        donateLayout.setAlignment(label1, QtCore.Qt.AlignLeft)
        donateLayout.addWidget(self.donateLimitBox)
        donateLayout.setAlignment(self.donateLimitBox, QtCore.Qt.AlignLeft)
        label2 = QLabel(" BTC as a donation.")
        donateLayout.addWidget(label2)
        label2.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        donateLayout.setAlignment(label2, QtCore.Qt.AlignLeft)
        label3 = HelpLabel('More', donation_more_message,
                           'About the donation feature')
        label3.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        donateLayout.setAlignment(label3, QtCore.Qt.AlignLeft)
        donateLayout.addWidget(label3)
        donateLayout.addStretch(1)
        return donateLayout

    def initUI(self):
        vbox = QVBoxLayout(self)
        top = QFrame()
        top.setFrameShape(QFrame.StyledPanel)
        topLayout = QGridLayout()
        top.setLayout(topLayout)
        sA = QScrollArea()
        sA.setWidgetResizable(True)
        topLayout.addWidget(sA)
        self.qtw = QTabWidget()
        sA.setWidget(self.qtw)
        self.single_join_tab = QWidget()
        self.schedule_tab = QWidget()
        self.qtw.addTab(self.single_join_tab, "Single Join")
        self.qtw.addTab(self.schedule_tab, "Multiple Join")

        #construct layout for scheduler
        sch_layout = QGridLayout()
        sch_layout.setSpacing(4)
        self.schedule_tab.setLayout(sch_layout)
        current_schedule_layout = QVBoxLayout()
        sch_label1=QLabel("Current schedule: ")
        sch_label1.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.sch_label2 = QLabel("None")
        current_schedule_layout.addWidget(sch_label1)
        current_schedule_layout.addWidget(self.sch_label2)
        self.sched_view = QTextEdit()
        self.sched_view.setReadOnly(True)
        self.sched_view.setLineWrapMode(QTextEdit.NoWrap)
        current_schedule_layout.addWidget(self.sched_view)
        sch_layout.addLayout(current_schedule_layout, 0, 0, 1, 1)
        self.schedule_set_button = QPushButton('Choose schedule file')
        self.schedule_set_button.clicked.connect(self.selectSchedule)
        self.schedule_generate_button = QPushButton('Generate tumble schedule')
        self.schedule_generate_button.clicked.connect(self.generateTumbleSchedule)
        self.sch_startButton = QPushButton('Run schedule')
        self.sch_startButton.setEnabled(False) #not runnable until schedule chosen
        self.sch_startButton.clicked.connect(self.startMultiple)
        self.sch_abortButton = QPushButton('Abort')
        self.sch_abortButton.setEnabled(False)
        self.sch_abortButton.clicked.connect(self.abortTransactions)

        sch_buttons_box = QGroupBox("Actions")
        sch_buttons_layout = QVBoxLayout()
        sch_buttons_layout.addWidget(self.schedule_set_button)
        sch_buttons_layout.addWidget(self.schedule_generate_button)
        sch_buttons_layout.addWidget(self.sch_startButton)
        sch_buttons_layout.addWidget(self.sch_abortButton)
        sch_buttons_box.setLayout(sch_buttons_layout)
        sch_layout.addWidget(sch_buttons_box, 0, 1, 1, 1)

        innerTopLayout = QGridLayout()
        innerTopLayout.setSpacing(4)
        self.single_join_tab.setLayout(innerTopLayout)

        donateLayout = self.getDonateLayout()
        innerTopLayout.addLayout(donateLayout, 0, 0, 1, 2)
        self.widgets = getSettingsWidgets()
        for i, x in enumerate(self.widgets):
            innerTopLayout.addWidget(x[0], i + 1, 0)
            innerTopLayout.addWidget(x[1], i + 1, 1, 1, 2)
        self.widgets[0][1].editingFinished.connect(
            lambda: checkAddress(self, self.widgets[0][1].text()))
        self.startButton = QPushButton('Start')
        self.startButton.setToolTip(
            'If "checktx" is selected in the Settings, you will be \n'
            'prompted to decide whether to accept\n'
            'the transaction after connecting, and shown the\n'
            'fees to pay; you can cancel at that point, or by \n'
             'pressing "Abort".')
        self.startButton.clicked.connect(self.startSingle)
        self.abortButton = QPushButton('Abort')
        self.abortButton.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.startButton)
        buttons.addWidget(self.abortButton)
        self.abortButton.clicked.connect(self.abortTransactions)
        innerTopLayout.addLayout(buttons, len(self.widgets) + 1, 0, 1, 2)
        splitter1 = QSplitter(QtCore.Qt.Vertical)
        self.textedit = QTextEdit()
        self.textedit.verticalScrollBar().rangeChanged.connect(
            self.resizeScroll)
        XStream.stdout().messageWritten.connect(self.updateConsoleText)
        XStream.stderr().messageWritten.connect(self.updateConsoleText)
        splitter1.addWidget(top)
        splitter1.addWidget(self.textedit)
        splitter1.setSizes([400, 200])
        self.setLayout(vbox)
        vbox.addWidget(splitter1)
        self.show()

    def updateConsoleText(self, txt):
        #these alerts are a bit suboptimal;
        #colored is better, and in the ultra-rare
        #case of getting both, one will be swallowed.
        #However, the transaction confirmation dialog
        #will at least show both in RED and BOLD, and they will be more prominent.
        #TODO in new daemon this is not accessible? Or?
        """
        if joinmarket_alert[0]:
            w.statusBar().showMessage("JOINMARKET ALERT: " + joinmarket_alert[
                0])
        if core_alert[0]:
            w.statusBar().showMessage("BITCOIN CORE ALERT: " + core_alert[0])
        """
        self.textedit.insertPlainText(txt)

    def resizeScroll(self, mini, maxi):
        self.textedit.verticalScrollBar().setValue(maxi)

    def restartWaitWrap(self):
        if restart_wait(self.waitingtxid):
            self.restartTimer.stop()
            self.waitingtxid = None
            w.statusBar().showMessage("Transaction in a block, now continuing.")
            self.startJoin()

    def startMultiple(self):
        if not self.spendstate.runstate == 'ready':
            log.info("Cannot start join, already running.")
            return
        if not self.spendstate.loaded_schedule:
            log.info("Cannot start, no schedule loaded.")
            return
        self.spendstate.updateType('multiple')
        self.spendstate.updateRun('running')

        if self.tumbler_options:
            #Uses the flag 'True' value from selectSchedule to recognize a restart,
            #which needs new dynamic option values. The rationale for using input
            #is in case the user can increase success probability by changing them.
            if self.tumbler_options == True:
                wizard = TumbleRestartWizard()
                wizard_return = wizard.exec_()
                if wizard_return == QDialog.Rejected:
                    return
                self.tumbler_options = wizard.getOptions()
            #check for a partially-complete schedule; if so,
            #follow restart logic
            #1. filter out complete:
            self.spendstate.loaded_schedule = [
                s for s in self.spendstate.loaded_schedule if s[5] != 1]
            #reload destination addresses
            self.tumbler_destaddrs = [x[3] for x in self.spendstate.loaded_schedule
                                     if x not in ["INTERNAL", "addrask"]]
            #2 Check for unconfirmed
            if isinstance(self.spendstate.loaded_schedule[0][5], str) and len(
                self.spendstate.loaded_schedule[0][5]) == 64:
                #ensure last transaction is confirmed before restart
                tumble_log.info("WAITING TO RESTART...")
                w.statusBar().showMessage("Waiting for confirmation to restart..")
                txid = self.spendstate.loaded_schedule[0][5]
                #remove the already-done entry (this connects to the other TODO,
                #probably better *not* to truncate the done-already txs from file,
                #but simplest for now.
                self.spendstate.loaded_schedule = self.spendstate.loaded_schedule[1:]
                #defers startJoin() call until tx seen on network. Note that
                #since we already updated state to running, user cannot
                #start another transactions while waiting. Also, use :0 because
                #it always exists
                self.waitingtxid=txid+":0"
                self.restartTimer.timeout.connect(self.restartWaitWrap)
                self.restartTimer.start(5000)
                return
            self.updateSchedView()
        self.startJoin()

    def checkDirectSend(self, dtx, destaddr, amount, fee):
        """Give user info to decide whether to accept a direct send;
        note the callback includes the full prettified transaction,
        but currently not printing it for space reasons.
        """
        mbinfo = ["Sending " + satoshis_to_amt_str(amount) + ",",
                  "to: " + destaddr + ",",
                  "Fee: " + satoshis_to_amt_str(fee) + ".",
                  "Accept?"]
        reply = JMQtMessageBox(self, '\n'.join([m + '<p>' for m in mbinfo]),
                               mbtype='question', title="Direct send")
        if reply == QMessageBox.Yes:
            return True
        else:
            return False

    def infoDirectSend(self, txid):
        JMQtMessageBox(self, "Tx sent: " + str(txid), title="Success")

    def startSingle(self):
        if not self.spendstate.runstate == 'ready':
            log.info("Cannot start join, already running.")
        if not self.validateSettings():
            return
        destaddr = str(self.widgets[0][1].text())
        #convert from bitcoins (enforced by QDoubleValidator) to satoshis
        btc_amount_str = str(self.widgets[3][1].text())
        amount = int(Decimal(btc_amount_str) * Decimal('1e8'))
        makercount = int(self.widgets[1][1].text())
        mixdepth = int(self.widgets[2][1].text())
        if makercount == 0:
            txid = direct_send(w.wallet, amount, mixdepth,
                                  destaddr, accept_callback=self.checkDirectSend,
                                  info_callback=self.infoDirectSend)
            if not txid:
                self.giveUp()
            else:
                self.persistTxToHistory(destaddr, amount, txid)
                self.cleanUp()
            return

        #note 'amount' is integer, so not interpreted as fraction
        #see notes in sample testnet schedule for format
        self.spendstate.loaded_schedule = [[mixdepth, amount, makercount,
                                            destaddr, 0, 0]]
        self.spendstate.updateType('single')
        self.spendstate.updateRun('running')
        self.startJoin()

    def startJoin(self):
        if not w.wallet:
            JMQtMessageBox(self, "Cannot start without a loaded wallet.",
                           mbtype="crit", title="Error")
            return
        if jm_single().config.get("BLOCKCHAIN",
                                  "blockchain_source") == 'blockr':
            res = self.showBlockrWarning()
            if res == True:
                return

        #all settings are valid; start
        #dialog removed for now, annoying, may review later
        #JMQtMessageBox(
        #    self,
        #    "Connecting to IRC.\nView real-time log in the lower pane.",
        #    title="Coinjoin starting")

        log.debug('starting coinjoin ..')

        #DON'T sync wallet since unless cache is updated, will forget index
        #w.statusBar().showMessage("Syncing wallet ...")
        #sync_wallet(w.wallet, fast=True)

        #Decide whether to interrupt processing to sanity check the fees
        if self.tumbler_options:
            check_offers_callback = self.checkOffersTumbler
        elif jm_single().config.get("GUI", "checktx") == "true":
            check_offers_callback = self.callback_checkOffers
        else:
            check_offers_callback = None

        destaddrs = self.tumbler_destaddrs if self.tumbler_options else []
        self.taker = Taker(w.wallet,
                           self.spendstate.loaded_schedule,
                           order_chooser=weighted_order_choose,
                           callbacks=[check_offers_callback,
                                      self.callback_takerInfo,
                                      self.callback_takerFinished],
                           tdestaddrs=destaddrs,
                           ignored_makers=ignored_makers)
        if not self.clientfactory:
            #First run means we need to start: create clientfactory
            #and start reactor Thread
            self.clientfactory = JMTakerClientProtocolFactory(self.taker)
            thread = TaskThread(self)
            daemon = jm_single().config.getint("DAEMON", "no_daemon")
            daemon = True if daemon == 1 else False
            thread.add(partial(start_reactor,
                   "localhost",
                   jm_single().config.getint("GUI", "daemon_port"),
                   self.clientfactory,
                   ish=False,
                   daemon=daemon))
        else:
            #This will re-use IRC connections in background (daemon), no restart
            self.clientfactory.getClient().taker = self.taker
            self.clientfactory.getClient().clientStart()
        w.statusBar().showMessage("Connecting to IRC ...")

    def callback_checkOffers(self, offers_fee, cjamount):
        """Receives the signal from the JMClient thread
        """
        if self.taker.aborted:
            log.debug("Not processing offers, user has aborted.")
            return False
        self.offers_fee = offers_fee
        self.jmclient_obj.emit(QtCore.SIGNAL('JMCLIENT:offers'))
        #The JMClient thread must wait for user input
        while not self.filter_offers_response:
            time.sleep(0.1)
        if self.filter_offers_response == "ACCEPT":
            self.filter_offers_response = None
            #The user is now committed to the transaction
            self.abortButton.setEnabled(False)
            return True
        self.filter_offers_response = None
        return False

    def callback_takerInfo(self, infotype, infomsg):
        if infotype == "ABORT":
            self.taker_info_type = 'warn'
        elif infotype == "INFO":
            self.taker_info_type = 'info'
        else:
            raise NotImplementedError
        self.taker_infomsg = infomsg
        self.jmclient_obj.emit(QtCore.SIGNAL('JMCLIENT:info'))
        while not self.taker_info_response:
            time.sleep(0.1)
        #No need to check response type, only OK for msgbox
        self.taker_info_response = None
        return

    def callback_takerFinished(self, res, fromtx=False, waittime=0.0,
                               txdetails=None):
        self.taker_finished_res = res
        self.taker_finished_fromtx = fromtx
        self.taker_finished_waittime = waittime
        self.taker_finished_txdetails = txdetails
        self.jmclient_obj.emit(QtCore.SIGNAL('JMCLIENT:finished'))
        return

    def takerInfo(self):
        if self.taker_info_type == "info":
            #cannot use dialogs that interrupt gui thread here
            if len(self.taker_infomsg) > 200:
                log.info("INFO: " + self.taker_infomsg)
            else:
                w.statusBar().showMessage(self.taker_infomsg)
        elif self.taker_info_type == "warn":
            JMQtMessageBox(self, self.taker_infomsg,
                           mbtype=self.taker_info_type)
            #Abort signal explicitly means this transaction will not continue.
            self.abortTransactions()
        self.taker_info_response = True

    def checkOffersTumbler(self, offers_fees, cjamount):
        return tumbler_filter_orders_callback(offers_fees, cjamount,
                                              self.taker, self.tumbler_options)

    def checkOffers(self):
        """Parse offers and total fee from client protocol,
        allow the user to agree or decide.
        """
        if not self.offers_fee:
            JMQtMessageBox(self,
                           "Not enough matching offers found.",
                           mbtype='warn',
                           title="Error")
            self.giveUp()
            return
        offers, total_cj_fee = self.offers_fee
        total_fee_pc = 1.0 * total_cj_fee / self.taker.cjamount
        #Note this will be a new value if sweep, else same as previously entered
        btc_amount_str = satoshis_to_amt_str(self.taker.cjamount)

        #TODO separate this out into a function
        mbinfo = []
        #See note above re: alerts
        """
        if joinmarket_alert[0]:
            mbinfo.append("<b><font color=red>JOINMARKET ALERT: " +
                          joinmarket_alert[0] + "</font></b>")
            mbinfo.append(" ")
        if core_alert[0]:
            mbinfo.append("<b><font color=red>BITCOIN CORE ALERT: " +
                          core_alert[0] + "</font></b>")
            mbinfo.append(" ")
        """
        mbinfo.append("Sending amount: " + btc_amount_str)
        mbinfo.append("to address: " + self.taker.my_cj_addr)
        mbinfo.append(" ")
        mbinfo.append("Counterparties chosen:")
        mbinfo.append('Name,     Order id, Coinjoin fee (sat.)')
        for k, o in offers.iteritems():
            if o['ordertype'] == 'reloffer':
                display_fee = int(self.taker.cjamount *
                                  float(o['cjfee'])) - int(o['txfee'])
            elif o['ordertype'] == 'absoffer':
                display_fee = int(o['cjfee']) - int(o['txfee'])
            else:
                log.debug("Unsupported order type: " + str(o['ordertype']) +
                          ", aborting.")
                self.giveUp()
                return False
            mbinfo.append(k + ', ' + str(o['oid']) + ',         ' + str(
                display_fee))
        mbinfo.append('Total coinjoin fee = ' + str(total_cj_fee) +
                      ' satoshis, or ' + str(float('%.3g' % (
                          100.0 * total_fee_pc))) + '%')
        title = 'Check Transaction'
        if total_fee_pc * 100 > jm_single().config.getint("GUI",
                                                          "check_high_fee"):
            title += ': WARNING: Fee is HIGH!!'
        reply = JMQtMessageBox(self,
                               '\n'.join([m + '<p>' for m in mbinfo]),
                               mbtype='question',
                               title=title)
        if reply == QMessageBox.Yes:
            #amount is now accepted; pass control back to reactor
            self.filter_offers_response = "ACCEPT"
        else:
            self.filter_offers_response = "REJECT"
            self.giveUp()

    def startNextTransaction(self):
        #sync_wallet(w.wallet, fast=True)
        self.clientfactory.getClient().clientStart()

    def takerFinished(self):
        """Callback (after pass-through signal) for jmclient.Taker
        on completion of each join transaction.
        """
        #non-GUI-specific state updates first:
        if self.tumbler_options:
            sfile = os.path.join(logsdir, 'TUMBLE.schedule')
            tumbler_taker_finished_update(self.taker, sfile, tumble_log,
                                      self.tumbler_options, self.taker_finished_res,
                                      self.taker_finished_fromtx,
                                      self.taker_finished_waittime,
                                      self.taker_finished_txdetails)

        self.spendstate.loaded_schedule = self.taker.schedule
        #Shows the schedule updates in the GUI; TODO make this more visual
        if self.spendstate.typestate == 'multiple':
            self.updateSchedView()

        #GUI-specific updates; QTimer.singleShot serves the role
        #of reactor.callLater
        if self.taker_finished_fromtx == "unconfirmed":
            w.statusBar().showMessage(
                "Transaction seen on network: " + self.taker.txid)
            if self.spendstate.typestate == 'single':
                JMQtMessageBox(self, "Transaction broadcast OK. You can safely \n"
                               "shut down if you don't want to wait.",
                               title="Success")
            #TODO: theoretically possible to miss this if confirmed event
            #seen before unconfirmed.
            self.persistTxToHistory(self.taker.my_cj_addr, self.taker.cjamount,
                                                        self.taker.txid)

            #TODO prob best to completely fold multiple and tumble to reduce
            #complexity/duplication
            if self.spendstate.typestate == 'multiple' and not self.tumbler_options:
                self.taker.wallet.update_cache_index()
            return
        if self.taker_finished_fromtx:
            if self.taker_finished_res:
                w.statusBar().showMessage("Transaction confirmed: " + self.taker.txid)
                #singleShot argument is in milliseconds
                if self.nextTxTimer:
                    self.nextTxTimer.stop()
                self.nextTxTimer = QtCore.QTimer()
                self.nextTxTimer.setSingleShot(True)
                self.nextTxTimer.timeout.connect(self.startNextTransaction)
                self.nextTxTimer.start(int(self.taker_finished_waittime*60*1000))
                #QtCore.QTimer.singleShot(int(self.taker_finished_waittime*60*1000),
                #                         self.startNextTransaction)
                #see note above re multiple/tumble duplication
                if self.spendstate.typestate == 'multiple' and \
                   not self.tumbler_options:
                    txd, txid = self.taker_finished_txdetails
                    self.taker.wallet.remove_old_utxos(txd)
                    self.taker.wallet.add_new_utxos(txd, txid)
            else:
                if self.tumbler_options:
                    w.statusBar().showMessage("Transaction failed, trying again...")
                    QtCore.QTimer.singleShot(0, self.startNextTransaction)
                else:
                    #currently does not continue for non-tumble schedules
                    self.giveUp()
        else:
            if self.taker_finished_res:
                w.statusBar().showMessage("All transaction(s) completed successfully.")
                if len(self.taker.schedule) == 1:
                    msg = "Transaction has been confirmed.\n" + "Txid: " + \
                                           str(self.taker.txid)
                else:
                    msg = "All transactions have been confirmed."
                JMQtMessageBox(self, msg, title="Success")
                self.cleanUp()
            else:
                self.giveUp()

    def persistTxToHistory(self, addr, amt, txid):
        #persist the transaction to history
        with open(jm_single().config.get("GUI", "history_file"), 'ab') as f:
            f.write(','.join([addr, satoshis_to_amt_str(amt), txid,
                              datetime.datetime.now(
                                  ).strftime("%Y/%m/%d %H:%M:%S")]))
            f.write('\n')  #TODO: Windows
        #update the TxHistory tab
        txhist = w.centralWidget().widget(3)
        txhist.updateTxInfo()

    def toggleButtons(self):
        """Refreshes accessibility of buttons in the (single, multiple) join
        tabs based on the current state as defined by the SpendStateMgr instance.
        Thus, should always be called on any update to that instance.
        """
        #The first two buttons are for the single join tab; the remaining 4
        #are for the multijoin tab.
        btns = (self.startButton, self.abortButton,
                self.schedule_set_button, self.schedule_generate_button,
                self.sch_startButton, self.sch_abortButton)
        if self.spendstate.runstate == 'ready':
            btnsettings = (True, False, True, True, True, False)
        elif self.spendstate.runstate == 'running':
            if self.spendstate.typestate == 'single':
                #can only abort current run, nothing else
                btnsettings = (False, True, False, False, False, False)
            elif self.spendstate.typestate == 'multiple':
                btnsettings = (False, False, False, False, False, True)
            else:
                assert False
        else:
            assert False

        for b, s in zip(btns, btnsettings):
            b.setEnabled(s)

    def abortTransactions(self):
        self.taker.aborted = True
        self.giveUp()

    def giveUp(self):
        """Inform the user that the transaction failed, then reset state.
        """
        log.debug("Transaction aborted.")
        w.statusBar().showMessage("Transaction aborted.")
        if self.taker and len(self.taker.ignored_makers) > 0:
            JMQtMessageBox(self, "These Makers did not respond, and will be \n"
                           "ignored in future: \n" + str(
                            ','.join(self.taker.ignored_makers)),
                           title="Transaction aborted")
            ignored_makers.extend(self.taker.ignored_makers)
        self.cleanUp()

    def cleanUp(self):
        """Reset state to 'ready'
        """
        #Qt specific: because schedules can restart in same app instance,
        #we must clean up any existing delayed actions via singleShot.
        #Currently this should only happen via self.abortTransactions.
        if self.nextTxTimer:
            self.nextTxTimer.stop()
        self.spendstate.reset()
        self.tumbler_options = None
        self.tumbler_destaddrs = None

    def validateSettings(self):
        valid, errmsg = validate_address(self.widgets[0][1].text())
        if not valid:
            JMQtMessageBox(self, errmsg, mbtype='warn', title="Error")
            return False
        errs = ["Non-zero number of counterparties must be provided.",
                "Mixdepth must be chosen.",
                "Amount, in bitcoins, must be provided."]
        for i in range(1, 4):
            if self.widgets[i][1].text().size() == 0:
                JMQtMessageBox(self, errs[i - 1], mbtype='warn', title="Error")
                return False
        if not w.wallet:
            JMQtMessageBox(self,
                           "There is no wallet loaded.",
                           mbtype='warn',
                           title="Error")
            return False
        return True

    def showBlockrWarning(self):
        if jm_single().config.getint("GUI", "privacy_warning") == 0:
            return False
        qmb = QMessageBox()
        qmb.setIcon(QMessageBox.Warning)
        qmb.setWindowTitle("Privacy Warning")
        qcb = QCheckBox("Don't show this warning again.")
        lyt = qmb.layout()
        lyt.addWidget(QLabel(warnings['blockr_privacy']), 0, 1)
        lyt.addWidget(qcb, 1, 1)
        qmb.addButton(QPushButton("Continue"), QMessageBox.YesRole)
        qmb.addButton(QPushButton("Cancel"), QMessageBox.NoRole)

        qmb.exec_()

        switch_off_warning = '0' if qcb.isChecked() else '1'
        jm_single().config.set("GUI", "privacy_warning", switch_off_warning)

        res = qmb.buttonRole(qmb.clickedButton())
        if res == QMessageBox.YesRole:
            return False
        elif res == QMessageBox.NoRole:
            return True
        else:
            log.debug("GUI error: unrecognized button, canceling.")
            return True


class TxHistoryTab(QWidget):

    def __init__(self):
        super(TxHistoryTab, self).__init__()
        self.initUI()

    def initUI(self):
        self.tHTW = MyTreeWidget(self, self.create_menu, self.getHeaders())
        self.tHTW.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tHTW.header().setResizeMode(QHeaderView.Interactive)
        self.tHTW.header().setStretchLastSection(False)
        self.tHTW.on_update = self.updateTxInfo
        vbox = QVBoxLayout()
        self.setLayout(vbox)
        vbox.setMargin(0)
        vbox.setSpacing(0)
        vbox.addWidget(self.tHTW)
        self.updateTxInfo()
        self.show()

    def getHeaders(self):
        '''Function included in case dynamic in future'''
        return ['Receiving address', 'Amount in BTC', 'Transaction id', 'Date']

    def updateTxInfo(self, txinfo=None):
        self.tHTW.clear()
        if not txinfo:
            txinfo = self.getTxInfoFromFile()
        for t in txinfo:
            t_item = QTreeWidgetItem(t)
            self.tHTW.addChild(t_item)
        for i in range(4):
            self.tHTW.resizeColumnToContents(i)

    def getTxInfoFromFile(self):
        hf = jm_single().config.get("GUI", "history_file")
        if not os.path.isfile(hf):
            if w:
                w.statusBar().showMessage("No transaction history found.")
            return []
        txhist = []
        with open(hf, 'rb') as f:
            txlines = f.readlines()
            for tl in txlines:
                txhist.append(tl.strip().split(','))
                if not len(txhist[-1]) == 4:
                    JMQtMessageBox(self,
                                   "Incorrectedly formatted file " + hf,
                                   mbtype='warn',
                                   title="Error")
                    w.statusBar().showMessage("No transaction history found.")
                    return []
        return txhist[::-1
                     ]  #appended to file in date order, window shows reverse

    def create_menu(self, position):
        item = self.tHTW.currentItem()
        if not item:
            return
        address_valid = False
        if item:
            address = str(item.text(0))
            try:
                btc.b58check_to_hex(address)
                address_valid = True
            except AssertionError:
                log.debug('no btc address found, not creating menu item')

        menu = QMenu()
        if address_valid:
            menu.addAction("Copy address to clipboard",
                           lambda: app.clipboard().setText(address))
        menu.addAction("Copy transaction id to clipboard",
                       lambda: app.clipboard().setText(str(item.text(2))))
        menu.addAction("Copy full tx info to clipboard",
                       lambda: app.clipboard().setText(
                           ','.join([str(item.text(_)) for _ in range(4)])))
        menu.exec_(self.tHTW.viewport().mapToGlobal(position))


class JMWalletTab(QWidget):

    def __init__(self):
        super(JMWalletTab, self).__init__()
        self.wallet_name = 'NONE'
        self.initUI()

    def initUI(self):
        self.label1 = QLabel(
            "CURRENT WALLET: " + self.wallet_name + ', total balance: 0.0',
            self)
        v = MyTreeWidget(self, self.create_menu, self.getHeaders())
        v.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.on_update = self.updateWalletInfo
        self.history = v
        vbox = QVBoxLayout()
        self.setLayout(vbox)
        vbox.setMargin(0)
        vbox.setSpacing(0)
        vbox.addWidget(self.label1)
        vbox.addWidget(v)
        buttons = QWidget()
        vbox.addWidget(buttons)
        self.updateWalletInfo()
        #vBoxLayout.addWidget(self.label2)
        #vBoxLayout.addWidget(self.table)
        self.show()

    def getHeaders(self):
        '''Function included in case dynamic in future'''
        return ['Address', 'Index', 'Balance', 'Used/New']

    def create_menu(self, position):
        item = self.history.currentItem()
        address_valid = False
        if item:
            address = str(item.text(0))
            try:
                btc.b58check_to_hex(address)
                address_valid = True
            except AssertionError:
                log.debug('no btc address found, not creating menu item')

        menu = QMenu()
        if address_valid:
            menu.addAction("Copy address to clipboard",
                           lambda: app.clipboard().setText(address))
        menu.addAction("Resync wallet from blockchain",
                       lambda: w.resyncWallet())
        #TODO add more items to context menu
        menu.exec_(self.history.viewport().mapToGlobal(position))

    def updateWalletInfo(self, walletinfo=None):
        l = self.history
        l.clear()
        if walletinfo:
            self.mainwindow = self.parent().parent().parent()
            rows, mbalances, total_bal = walletinfo
            if get_network() == 'testnet':
                self.wallet_name = self.mainwindow.wallet.seed
            else:
                self.wallet_name = os.path.basename(self.mainwindow.wallet.path)
            self.label1.setText("CURRENT WALLET: " + self.wallet_name +
                                ', total balance: ' + total_bal)

        for i in range(jm_single().config.getint("GUI", "max_mix_depth")):
            if walletinfo:
                mdbalance = mbalances[i]
            else:
                mdbalance = "{0:.8f}".format(0)
            m_item = QTreeWidgetItem(["Mixdepth " + str(i) + " , balance: " +
                                      mdbalance, '', '', '', ''])
            l.addChild(m_item)
            for forchange in [0, 1]:
                heading = 'EXTERNAL' if forchange == 0 else 'INTERNAL'
                heading_end = ' addresses m/0/%d/%d/' % (i, forchange)
                heading += heading_end
                seq_item = QTreeWidgetItem([heading, '', '', '', ''])
                m_item.addChild(seq_item)
                if not forchange:
                    seq_item.setExpanded(True)
                if not walletinfo:
                    item = QTreeWidgetItem(['None', '', '', ''])
                    seq_item.addChild(item)
                else:
                    for j in range(len(rows[i][forchange])):
                        item = QTreeWidgetItem(rows[i][forchange][j])
                        item.setFont(0, QFont(MONOSPACE_FONT))
                        if rows[i][forchange][j][3] == 'used':
                            item.setForeground(3, QBrush(QColor('red')))
                        seq_item.addChild(item)


class JMMainWindow(QMainWindow):

    def __init__(self):
        super(JMMainWindow, self).__init__()
        self.wallet = None
        self.initUI()

    def closeEvent(self, event):
        quit_msg = "Are you sure you want to quit?"
        reply = JMQtMessageBox(self, quit_msg, mbtype='question')
        if reply == QMessageBox.Yes:
            persist_config()
            event.accept()
        else:
            event.ignore()

    def initUI(self):
        self.statusBar().showMessage("Ready")
        self.setGeometry(300, 300, 250, 150)
        exitAction = QAction(QIcon('exit.png'), '&Exit', self)
        exitAction.setShortcut('Ctrl+Q')
        exitAction.setStatusTip('Exit application')
        exitAction.triggered.connect(qApp.quit)
        generateAction = QAction('&Generate', self)
        generateAction.setStatusTip('Generate new wallet')
        generateAction.triggered.connect(self.generateWallet)
        loadAction = QAction('&Load', self)
        loadAction.setStatusTip('Load wallet from file')
        loadAction.triggered.connect(self.selectWallet)
        recoverAction = QAction('&Recover', self)
        recoverAction.setStatusTip('Recover wallet from seedphrase')
        recoverAction.triggered.connect(self.recoverWallet)
        aboutAction = QAction('About Joinmarket', self)
        aboutAction.triggered.connect(self.showAboutDialog)
        exportPrivAction = QAction('&Export keys', self)
        exportPrivAction.setStatusTip('Export all private keys to a csv file')
        exportPrivAction.triggered.connect(self.exportPrivkeysCsv)
        menubar = QMenuBar()

        walletMenu = menubar.addMenu('&Wallet')
        walletMenu.addAction(loadAction)
        walletMenu.addAction(generateAction)
        walletMenu.addAction(recoverAction)
        walletMenu.addAction(exportPrivAction)
        walletMenu.addAction(exitAction)
        aboutMenu = menubar.addMenu('&About')
        aboutMenu.addAction(aboutAction)

        self.setMenuBar(menubar)
        self.show()

    def showAboutDialog(self):
        msgbox = QDialog(self)
        lyt = QVBoxLayout(msgbox)
        msgbox.setWindowTitle(appWindowTitle)
        label1 = QLabel()
        label1.setText(
            "<a href=" + "'https://github.com/joinmarket-org/joinmarket/wiki'>"
            + "Read more about Joinmarket</a><p>" + "<p>".join(
                ["Joinmarket core software version: " + JM_CORE_VERSION,
                 "JoinmarketQt version: " + JM_GUI_VERSION,
                 "Messaging protocol version:" + " %s" % (
                     str(jm_single().JM_VERSION)
                 ), "Help us support Bitcoin fungibility -", "donate here: "]))
        label2 = QLabel(donation_address)
        for l in [label1, label2]:
            l.setTextFormat(QtCore.Qt.RichText)
            l.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
            l.setOpenExternalLinks(True)
        label2.setText("<a href='bitcoin:" + donation_address + "'>" +
                       donation_address + "</a>")
        lyt.addWidget(label1)
        lyt.addWidget(label2)
        btnbox = QDialogButtonBox(msgbox)
        btnbox.setStandardButtons(QDialogButtonBox.Ok)
        btnbox.accepted.connect(msgbox.accept)
        lyt.addWidget(btnbox)
        msgbox.exec_()

    def exportPrivkeysCsv(self):
        if not self.wallet:
            JMQtMessageBox(self,
                           "No wallet loaded.",
                           mbtype='crit',
                           title="Error")
            return
        #TODO add password protection; too critical
        d = QDialog(self)
        d.setWindowTitle('Private keys')
        d.setMinimumSize(850, 300)
        vbox = QVBoxLayout(d)

        msg = "%s\n%s\n%s" % (
            "WARNING: ALL your private keys are secret.",
            "Exposing a single private key can compromise your entire wallet!",
            "In particular, DO NOT use 'redeem private key' services proposed by third parties."
        )
        vbox.addWidget(QLabel(msg))
        e = QTextEdit()
        e.setReadOnly(True)
        vbox.addWidget(e)
        b = OkButton(d, 'Export')
        b.setEnabled(False)
        vbox.addLayout(Buttons(CancelButton(d), b))
        private_keys = {}
        #prepare list of addresses with non-zero balance
        #TODO: consider adding a 'export insanely huge amount'
        #option for anyone with gaplimit troubles, although
        #that is a complete mess for a user, mostly changing
        #the gaplimit in the Settings tab should address it.
        rows = get_wallet_printout(self.wallet)
        addresses = []
        for forchange in rows[0]:
            for mixdepth in forchange:
                for addr_info in mixdepth:
                    if float(addr_info[2]) > 0:
                        addresses.append(addr_info[0])
        done = False

        def privkeys_thread():
            for addr in addresses:
                time.sleep(0.1)
                if done:
                    break
                priv = self.wallet.get_key_from_addr(addr)
                private_keys[addr] = btc.wif_compressed_privkey(
                    priv,
                    vbyte=get_p2pk_vbyte())
                d.emit(QtCore.SIGNAL('computing_privkeys'))
            d.emit(QtCore.SIGNAL('show_privkeys'))

        def show_privkeys():
            s = "\n".join(map(lambda x: x[0] + "\t" + x[1], private_keys.items(
            )))
            e.setText(s)
            b.setEnabled(True)

        d.connect(
            d, QtCore.SIGNAL('computing_privkeys'),
            lambda: e.setText("Please wait... %d/%d" % (len(private_keys), len(addresses))))
        d.connect(d, QtCore.SIGNAL('show_privkeys'), show_privkeys)

        threading.Thread(target=privkeys_thread).start()
        if not d.exec_():
            done = True
            return
        privkeys_fn_base = 'joinmarket-private-keys'
        i = 0
        privkeys_fn = privkeys_fn_base
        while os.path.isfile(privkeys_fn + '.csv'):
            i += 1
            privkeys_fn = privkeys_fn_base + str(i)
        try:
            with open(privkeys_fn + '.csv', "w") as f:
                transaction = csv.writer(f)
                transaction.writerow(["address", "private_key"])
                for addr, pk in private_keys.items():
                    #sanity check
                    if not btc.privtoaddr(
                            btc.from_wif_privkey(pk,
                                                 vbyte=get_p2pk_vbyte()),
                            magicbyte=get_p2pk_vbyte()) == addr:
                        JMQtMessageBox(None, "Failed to create privkey export -" +\
                                       " critical error in key parsing.",
                                       mbtype='crit')
                        return
                    transaction.writerow(["%34s" % addr, pk])
        except (IOError, os.error), reason:
            export_error_label = "JoinmarketQt was unable to produce a private key-export."
            JMQtMessageBox(None,
                           export_error_label + "\n" + str(reason),
                           mbtype='crit',
                           title="Unable to create csv")

        except Exception as e:
            JMQtMessageBox(self, str(e), mbtype='crit', title="Error")
            return

        JMQtMessageBox(self,
                       "Private keys exported to: " + privkeys_fn + '.csv',
                       title="Success")

    def recoverWallet(self):
        if get_network() == 'testnet':
            JMQtMessageBox(self,
                           'recover from seedphrase not supported for testnet',
                           mbtype='crit',
                           title="Error")
            return
        d = QDialog(self)
        d.setModal(1)
        d.setWindowTitle('Recover from seed')
        layout = QGridLayout(d)
        message_e = QTextEdit()
        layout.addWidget(QLabel('Enter 12 words'), 0, 0)
        layout.addWidget(message_e, 1, 0)
        hbox = QHBoxLayout()
        buttonBox = QDialogButtonBox(self)
        buttonBox.setStandardButtons(QDialogButtonBox.Ok |
                                     QDialogButtonBox.Cancel)
        buttonBox.button(QDialogButtonBox.Ok).clicked.connect(d.accept)
        buttonBox.button(QDialogButtonBox.Cancel).clicked.connect(d.reject)
        hbox.addWidget(buttonBox)
        layout.addLayout(hbox, 3, 0)
        result = d.exec_()
        if result != QDialog.Accepted:
            return
        msg = str(message_e.toPlainText())
        words = msg.split()  #splits on any number of ws chars
        if not len(words) == 12:
            JMQtMessageBox(self,
                           "You did not provide 12 words, aborting.",
                           mbtype='warn',
                           title="Error")
        else:
            try:
                seed = mn_decode(words)
                self.initWallet(seed=seed)
            except ValueError as e:
                JMQtMessageBox(self,
                               "Could not decode seedphrase: " + repr(e),
                               mbtype='warn',
                               title="Error")

    def selectWallet(self, testnet_seed=None):
        if get_network() != 'testnet':
            current_path = os.path.dirname(os.path.realpath(__file__))
            if os.path.isdir(os.path.join(current_path, 'wallets')):
                current_path = os.path.join(current_path, 'wallets')
            firstarg = QFileDialog.getOpenFileName(self,
                                                   'Choose Wallet File',
                                                   directory=current_path)
            #TODO validate the file looks vaguely like a wallet file
            log.debug('Looking for wallet in: ' + firstarg)
            if not firstarg:
                return
            decrypted = False
            while not decrypted:
                text, ok = QInputDialog.getText(self,
                                                'Decrypt wallet',
                                                'Enter your password:',
                                                mode=QLineEdit.Password)
                if not ok:
                    return
                pwd = str(text).strip()
                decrypted = self.loadWalletFromBlockchain(firstarg, pwd)
        else:
            if not testnet_seed:
                testnet_seed, ok = QInputDialog.getText(self,
                                                        'Load Testnet wallet',
                                                        'Enter a testnet seed:',
                                                        mode=QLineEdit.Normal)
                if not ok:
                    return
            firstarg = str(testnet_seed)
            pwd = None
            #ignore return value as there is no decryption failure possible
            self.loadWalletFromBlockchain(firstarg, pwd)

    def loadWalletFromBlockchain(self, firstarg=None, pwd=None):
        if (firstarg and pwd) or (firstarg and get_network() == 'testnet'):
            try:
                self.wallet = Wallet(
                    str(firstarg),
                    pwd,
                    max_mix_depth=jm_single().config.getint(
                    "GUI", "max_mix_depth"),
                    gaplimit=jm_single().config.getint("GUI", "gaplimit"))
            except WalletError:
                JMQtMessageBox(self,
                               "Wrong password",
                               mbtype='warn',
                               title="Error")
                return False
        if 'listunspent_args' not in jm_single().config.options('POLICY'):
            jm_single().config.set('POLICY', 'listunspent_args', '[0]')
        assert self.wallet, "No wallet loaded"
        thread = TaskThread(self)
        task = partial(sync_wallet, self.wallet, True)
        thread.add(task, on_done=self.updateWalletInfo)
        self.statusBar().showMessage("Reading wallet from blockchain ...")
        return True

    def updateWalletInfo(self):
        t = self.centralWidget().widget(0)
        if not self.wallet:  #failure to sync in constructor means object is not created
            newstmsg = "Unable to sync wallet - see error in console."
        else:
            t.updateWalletInfo(get_wallet_printout(self.wallet))
            newstmsg = "Wallet synced successfully."
        self.statusBar().showMessage(newstmsg)

    def resyncWallet(self):
        if not self.wallet:
            JMQtMessageBox(self,
                           "No wallet loaded",
                           mbtype='warn',
                           title="Error")
            return
        self.loadWalletFromBlockchain()

    def generateWallet(self):
        log.debug('generating wallet')
        if get_network() == 'testnet':
            seed = self.getTestnetSeed()
            self.selectWallet(testnet_seed=seed)
        else:
            self.initWallet()

    def getTestnetSeed(self):
        text, ok = QInputDialog.getText(
            self, 'Testnet seed', 'Enter a string as seed (can be anything):')
        if not ok or not text:
            JMQtMessageBox(self,
                           "No seed entered, aborting",
                           mbtype='warn',
                           title="Error")
            return
        return str(text).strip()

    def initWallet(self, seed=None):
        '''Creates a new mainnet
        wallet
        '''
        if not seed:
            seed = btc.sha256(os.urandom(64))[:32]
            words = mn_encode(seed)
            mb = QMessageBox()
            seed_recovery_warning = [
                "WRITE DOWN THIS WALLET RECOVERY SEED.",
                "If you fail to do this, your funds are",
                "at risk. Do NOT ignore this step!!!"
            ]
            mb.setText("\n".join(seed_recovery_warning))
            mb.setInformativeText(' '.join(words))
            mb.setStandardButtons(QMessageBox.Ok)
            ret = mb.exec_()

        pd = PasswordDialog()
        while True:
            pd.exec_()
            if pd.new_pw.text() != pd.conf_pw.text():
                JMQtMessageBox(self,
                               "Passwords don't match.",
                               mbtype='warn',
                               title="Error")
                continue
            break

        walletfile = create_wallet_file(str(pd.new_pw.text()), seed)
        walletname, ok = QInputDialog.getText(self, 'Choose wallet name',
                                              'Enter wallet file name:',
                                              QLineEdit.Normal, "wallet.json")
        if not ok:
            JMQtMessageBox(self, "Create wallet aborted", mbtype='warn')
            return
        #create wallets subdir if it doesn't exist
        if not os.path.exists('wallets'):
            os.makedirs('wallets')
        walletpath = os.path.join('wallets', str(walletname))
        # Does a wallet with the same name exist?
        if os.path.isfile(walletpath):
            JMQtMessageBox(self,
                           walletpath + ' already exists. Aborting.',
                           mbtype='warn',
                           title="Error")
            return
        else:
            fd = open(walletpath, 'w')
            fd.write(walletfile)
            fd.close()
            JMQtMessageBox(self,
                           'Wallet saved to ' + str(walletname),
                           title="Wallet created")
            self.loadWalletFromBlockchain(
                str(walletname), str(pd.new_pw.text()))


def get_wallet_printout(wallet):
    """Given a joinmarket wallet, retrieve the list of
    addresses and corresponding balances to be displayed;
    this could/should be a re-used function for both
    command line and GUI.
    The format of the retrieved data is:
    rows: is of format [[[addr,index,bal,used],[addr,...]]*5,
    [[addr, index,..], [addr, index..]]*5]
    mbalances: is a simple array of 5 mixdepth balances
    total_balance: whole wallet
    Bitcoin amounts returned are in btc, not satoshis
    """
    rows = []
    mbalances = []
    total_balance = 0
    for m in range(wallet.max_mix_depth):
        rows.append([])
        balance_depth = 0
        for forchange in [0, 1]:
            rows[m].append([])
            for k in range(wallet.index[m][forchange] + jm_single(
            ).config.getint("GUI", "gaplimit")):
                addr = wallet.get_addr(m, forchange, k)
                balance = 0.0
                for addrvalue in wallet.unspent.values():
                    if addr == addrvalue['address']:
                        balance += addrvalue['value']
                balance_depth += balance
                used = ('used' if k < wallet.index[m][forchange] else 'new')
                if balance > 0.0 or (k >= wallet.index[m][forchange] and
                                     forchange == 0):
                    rows[m][forchange].append([addr, str(k), "{0:.8f}".format(
                        balance / 1e8), used])
        mbalances.append(balance_depth)
        total_balance += balance_depth

    return (rows, ["{0:.8f}".format(x / 1e8) for x in mbalances],
            "{0:.8f}".format(total_balance / 1e8))

################################
config_load_error = False
app = QApplication(sys.argv)
try:
    load_program_config()
except Exception as e:
    config_load_error = "Failed to setup joinmarket: "+repr(e)
    if "RPC" in repr(e):
        config_load_error += '\n'*3 + ''.join(
            ["Errors about failed RPC connections usually mean an incorrectly ",
             "configured instance of Bitcoin Core (e.g. it hasn't been started ",
             "or the rpc ports are not correct in your joinmarket.cfg or your ",
             "bitcoin.conf file; see the joinmarket wiki for configuration details."
             ])
    JMQtMessageBox(None, config_load_error, mbtype='crit', title='failed to load')
    exit(1)
update_config_for_gui()

#to allow testing of confirm/unconfirm callback for multiple txs
if isinstance(jm_single().bc_interface, RegtestBitcoinCoreInterface):
    jm_single().bc_interface.tick_forward_chain_interval = 10
    jm_single().maker_timeout_sec = 5
    #trigger start with a fake tx
    jm_single().bc_interface.pushtx("00"*20)

#prepare for logging
for dname in ['logs', 'wallets', 'cmtdata']:
    if not os.path.exists(dname):
        os.makedirs(dname)
logsdir = os.path.join(os.path.dirname(jm_single().config_location), "logs")
#tumble log will not always be used, but is made available anyway:
tumble_log = get_tumble_log(logsdir)
#ignored makers list persisted across entire app run
ignored_makers = []
appWindowTitle = 'JoinMarketQt'
w = JMMainWindow()
tabWidget = QTabWidget(w)
tabWidget.addTab(JMWalletTab(), "JM Wallet")
settingsTab = SettingsTab()
tabWidget.addTab(settingsTab, "Settings")
tabWidget.addTab(SpendTab(), "Coinjoins")
tabWidget.addTab(TxHistoryTab(), "Tx History")
w.resize(600, 500)
suffix = ' - Testnet' if get_network() == 'testnet' else ''
w.setWindowTitle(appWindowTitle + suffix)
tabWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
w.setCentralWidget(tabWidget)
w.show()

sys.exit(app.exec_())
