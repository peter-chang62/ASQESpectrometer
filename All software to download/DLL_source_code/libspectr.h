#ifndef LIBSHARED_AND_STATIC_EXPORT_H
#define LIBSHARED_AND_STATIC_EXPORT_H

#ifdef LIBSHARED_AND_STATIC_STATIC_DEFINE
#  define LIBSHARED_AND_STATIC_EXPORT
#  define LIBSHARED_AND_STATIC_NO_EXPORT
#else
#  ifndef LIBSHARED_AND_STATIC_EXPORT
#    ifdef spectrlib_shared_EXPORTS
#      define LIBSHARED_AND_STATIC_EXPORT __declspec(dllexport)
#    else
        /* We are using this library */
#      define LIBSHARED_AND_STATIC_EXPORT __declspec(dllimport)
#    endif
#  endif

#  ifndef LIBSHARED_AND_STATIC_NO_EXPORT
#    define LIBSHARED_AND_STATIC_NO_EXPORT
#  endif
#endif

#ifndef LIBSHARED_AND_STATIC_DEPRECATED
#  define LIBSHARED_AND_STATIC_DEPRECATED __declspec(deprecated)
#  define LIBSHARED_AND_STATIC_DEPRECATED_EXPORT LIBSHARED_AND_STATIC_EXPORT __declspec(deprecated)
#  define LIBSHARED_AND_STATIC_DEPRECATED_NO_EXPORT LIBSHARED_AND_STATIC_NO_EXPORT __declspec(deprecated)
#endif

#define DEFINE_NO_DEPRECATED 0
#if DEFINE_NO_DEPRECATED
# define LIBSHARED_AND_STATIC_NO_DEPRECATED
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;

LIBSHARED_AND_STATIC_EXPORT int connectToDevice(const char* serialNumber);
LIBSHARED_AND_STATIC_EXPORT void disconnectDevice();
LIBSHARED_AND_STATIC_EXPORT int setAcquisitionParameters(uint16_t numOfScans, uint16_t numOfBlankScans, uint8_t scanMode, uint32_t timeOfExposure);
LIBSHARED_AND_STATIC_EXPORT int setExposure(const uint32_t timeOfExposure, const uint8_t force);
LIBSHARED_AND_STATIC_EXPORT int setFrameFormat(const uint16_t numOfStartElement, const uint16_t numOfEndElement, const uint8_t reductionMode, uint16_t *numOfPixelsInFrame);
LIBSHARED_AND_STATIC_EXPORT int getFrameFormat(uint16_t *numOfStartElement, uint16_t *numOfEndElement, uint8_t *reductionMode, uint16_t *numOfPixelsInFrame);
LIBSHARED_AND_STATIC_EXPORT int triggerAcquisition();
LIBSHARED_AND_STATIC_EXPORT int setExternalTrigger(const uint8_t enableMode, const uint8_t signalFrontMode);
LIBSHARED_AND_STATIC_EXPORT int setOpticalTrigger(const uint8_t enableMode, const uint16_t pixel, const uint16_t threshold);

LIBSHARED_AND_STATIC_EXPORT int getStatus(uint8_t *statusFlags, uint16_t *framesInMemory);
LIBSHARED_AND_STATIC_EXPORT int clearMemory();

LIBSHARED_AND_STATIC_EXPORT int getFrame(uint16_t  *framePixelsBuffer, const uint16_t numOfFrame);

LIBSHARED_AND_STATIC_EXPORT int eraseFlash();
LIBSHARED_AND_STATIC_EXPORT int readFlash(uint8_t *buffer, uint32_t absoluteOffset, uint32_t bytesToRead);
LIBSHARED_AND_STATIC_EXPORT int writeFlash(uint8_t *buffer, uint32_t offset, uint32_t bytesToWrite);

LIBSHARED_AND_STATIC_EXPORT int resetDevice();
LIBSHARED_AND_STATIC_EXPORT int detachDevice();

LIBSHARED_AND_STATIC_EXPORT int getAcquisitionParameters(uint16_t* numOfScans, uint16_t* numOfBlankScans, uint8_t* scanMode, uint32_t* timeOfExposure);

LIBSHARED_AND_STATIC_EXPORT int setAllParameters(uint16_t numOfScans, uint16_t numOfBlankScans, uint8_t scanMode, uint32_t timeOfExposure, uint8_t enableMode, uint8_t signalFrontMode);


#define OK 0
#define CONNECT_ERROR_WRONG_ID 500
#define CONNECT_ERROR_NOT_FOUND 501
#define CONNECT_ERROR_FAILED 502
#define DEVICE_NOT_INITIALIZED 503
#define WRITING_PROCESS_FAILED 504
#define READING_PROCESS_FAILED 505
#define WRONG_ANSWER 506
#define GET_FRAME_REMAINING_PACKETS_ERROR 507
#define NUM_OF_PACKETS_IN_FRAME_ERROR 508
#define INPUT_PARAMETER_NOT_INITIALIZED 509
#define READ_FLASH_REMAINING_PACKETS_ERROR 510

#ifdef __cplusplus
}
#endif

#endif
